"""The setup page, served on 127.0.0.1, for filling in config.txt, checking it and running it."""

import json
import logging
import os
import secrets
import sys
import threading
import webbrowser
from collections import deque
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any

from . import __version__, commands
from .checks import run_all, send_test_text
from .config import KEYS, SETTINGS, beside_config, load_config, save_config, setting, word_list
from .mail import LoginError, MailboxError
from .poller import Poller
from .reply import DEFAULT_PLUGINS, DEFAULT_SMS_CHARS, Answerer
from .text import sms_segments

logger = logging.getLogger("remotesearch")

STATIC = ("barlow-condensed-500.woff2", "barlow-condensed-700.woff2")
MOST_BODY = 64 * 1024


class LogLines(logging.Handler):
    """The last few hundred log lines, for the Activity page."""

    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.lines: deque[dict[str, str]] = deque(maxlen=300)

    def emit(self, record: logging.LogRecord) -> None:
        when = datetime.fromtimestamp(record.created, UTC).isoformat(timespec="seconds")
        self.lines.append({"at": when, "level": record.levelname, "text": record.getMessage()})


class App:
    """What the page can see and do, with one poller at most running in the background."""

    def __init__(self, config_path: str | Path) -> None:
        self.path = Path(config_path)
        self.token = secrets.token_urlsafe(24)
        self.lock = threading.Lock()
        self.poller: Poller | None = None
        self.stop_event: threading.Event | None = None
        self.thread: threading.Thread | None = None
        self.started = ""
        self.dry_run = False
        self.error = ""
        self.plugin_error = ""
        self.starting = False
        self.saved_while_running = False
        self.log = LogLines()
        logger.addHandler(self.log)

    def config(self) -> dict[str, str]:
        return load_config(self.path)

    def running(self) -> bool:
        return bool(self.thread and self.thread.is_alive())

    def state(self) -> dict[str, Any]:
        config = self.config()
        fields = []
        for s in SETTINGS:
            value = config.get(s.key, "")
            fields.append(
                {
                    "key": s.key,
                    "section": s.section,
                    "label": s.label,
                    "hint": s.hint,
                    "secret": s.secret,
                    # A saved secret never goes back to the page, only whether there is one.
                    "value": "" if s.secret else value,
                    "saved": bool(value),
                    # load_config lets these win, so typing over them here would do nothing.
                    "fromEnv": bool(os.environ.get(s.key)),
                }
            )
        off = word_list(config, "COMMANDS_OFF")
        try:
            found = commands.available(beside_config(config, "PLUGINS_DIR", DEFAULT_PLUGINS))
            self.plugin_error = ""
        except SystemExit as exc:
            found = commands.available()
            self.plugin_error = str(exc)
        listed = []
        for entry in found:
            listed.append(
                {
                    "words": list(entry.words),
                    "help": entry.help,
                    "on": not (off & set(entry.words)),
                    "plugin": bool(entry.folder),
                }
            )
        return {
            "version": __version__,
            "configPath": str(self.path.resolve()),
            "configExists": self.path.exists(),
            "settings": fields,
            "commands": listed,
            **self.activity(),
        }

    def activity(self) -> dict[str, Any]:
        return {
            "running": self.running(),
            "dryRun": self.dry_run,
            "started": self.started if self.running() else "",
            "error": self.error or self.plugin_error,
            "restartNeeded": self.saved_while_running and self.running(),
            "recent": [cost(r) for r in self.poller.recent.copy()] if self.poller else [],
            "log": list(self.log.lines)[-120:],
        }

    def save(self, changes: dict[str, Any]) -> dict[str, Any]:
        clean: dict[str, str] = {}
        for key, value in changes.items():
            if key not in KEYS or not isinstance(value, str):
                raise ValueError(f"{key} isn't a setting this knows.")
            if "\n" in value or "\r" in value:
                raise ValueError(f"{key} has a line break in it, and each setting is one line.")
            clean[key] = value.strip()
        with self.lock:
            save_config(self.path, clean)
            self.saved_while_running = self.running() or self.starting
        return self.state()

    def ask(self, question: str) -> dict[str, Any]:
        config = self.config()
        limit = setting(config, "MAX_SMS_CHARS", DEFAULT_SMS_CHARS)
        pages = Answerer(config, limit).pages(question)
        return {
            "limit": limit,
            "pages": [{"text": p, "chars": len(p), "segments": sms_segments(p)} for p in pages],
        }

    def check(self) -> dict[str, Any]:
        return {"results": [r.as_dict() for r in run_all(self.config())]}

    def test_text(self, via: str) -> dict[str, Any]:
        return {"sent": send_test_text(self.config(), via)}

    def start(self, dry_run: bool) -> dict[str, Any]:
        with self.lock:
            if self.running() or self.starting:
                return self.activity()
            self.error = ""
            self.starting = True
        # Built outside the lock, as it can spend a while connecting to the mail server
        # and Save needs the lock meanwhile.
        try:
            poller = Poller(self.config(), source=self.path, dry_run=dry_run)
        finally:
            with self.lock:
                self.starting = False
        with self.lock:
            self.poller = poller
            self.stop_event = threading.Event()
            self.dry_run = dry_run
            self.started = datetime.now(UTC).isoformat(timespec="seconds")
            self.thread = threading.Thread(target=self._run, name="poller", daemon=True)
            self.thread.start()
        return self.activity()

    def _run(self) -> None:
        assert self.poller
        assert self.stop_event
        try:
            self.poller.run(self.stop_event)
        except LoginError as exc:
            self.error = f"{exc}. Check the app password on Setup."
        except Exception as exc:  # shown on the page, as nobody is watching a terminal
            logger.exception("The poller stopped")
            self.error = f"It stopped after an error it couldn't get past ({exc})."
        finally:
            self.poller.close()

    def stop(self) -> dict[str, Any]:
        with self.lock:
            if self.stop_event:
                self.stop_event.set()
            thread = self.thread
        if thread:
            thread.join(timeout=15)
        return self.activity()


def cost(row: dict[str, Any]) -> dict[str, Any]:
    """A row of Activity with what its first text cost."""
    first = row.get("reply", "")
    return row | {"chars": len(first), "segments": sms_segments(first) if first else 0}


ACTIONS = {
    "/api/state": lambda app, _: app.state(),
    "/api/activity": lambda app, _: app.activity(),
    "/api/save": lambda app, body: app.save(body.get("changes") or {}),
    "/api/ask": lambda app, body: app.ask(str(body.get("question", ""))),
    "/api/check": lambda app, _: app.check(),
    "/api/test-text": lambda app, body: app.test_text(str(body.get("via") or "")),
    "/api/start": lambda app, body: app.start(bool(body.get("dryRun"))),
    "/api/stop": lambda app, _: app.stop(),
}


def page(token: str) -> bytes:
    html = resources.files(__package__).joinpath("ui.html").read_text(encoding="utf-8")
    return html.replace("{{TOKEN}}", token).encode("utf-8")


def handler(app: App, port: int) -> type[BaseHTTPRequestHandler]:
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    if port == 80:  # browsers leave the default port out of Host
        hosts |= {"127.0.0.1", "localhost"}

    class Handler(BaseHTTPRequestHandler):
        server_version = f"RemoteSearch/{__version__}"

        def log_message(self, format: str, *args: Any) -> None:
            logger.debug("%s %s", self.address_string(), format % args)

        def send(self, status: int, body: bytes, kind: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                "script-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                "frame-ancestors 'none'; form-action 'none'",
            )
            self.end_headers()
            self.wfile.write(body)

        def send_json(self, status: int, data: Any) -> None:
            self.send(status, json.dumps(data).encode("utf-8"), "application/json")

        def trusted_host(self) -> bool:
            # A page on any other site can't reach this, as a rebound DNS name or a
            # cross-site request arrives with some other Host.
            if self.headers.get("Host", "") in hosts:
                return True
            self.send_json(HTTPStatus.FORBIDDEN, {"error": "This page only answers on 127.0.0.1."})
            return False

        def do_GET(self) -> None:
            if not self.trusted_host():
                return
            if self.path in ("/", "/index.html"):
                self.send(HTTPStatus.OK, page(app.token), "text/html; charset=utf-8")
            elif self.path.removeprefix("/static/") in STATIC:
                name = self.path.removeprefix("/static/")
                data = resources.files(__package__).joinpath("static", name).read_bytes()
                self.send(HTTPStatus.OK, data, "font/woff2")
            else:
                self.send_json(HTTPStatus.NOT_FOUND, {"error": f"There's nothing at {self.path}."})

        def do_POST(self) -> None:
            if not self.trusted_host():
                return
            action = ACTIONS.get(self.path)
            if not action:
                self.send_json(HTTPStatus.NOT_FOUND, {"error": f"There's nothing at {self.path}."})
                return
            # A form on another site can post here but can't set this header.
            if not secrets.compare_digest(self.headers.get("X-Token", ""), app.token):
                self.send_json(HTTPStatus.FORBIDDEN, {"error": "Reload the page to get a new key."})
                return
            size = int(self.headers.get("Content-Length") or 0)
            if size > MOST_BODY:
                self.send_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "That's too big."})
                return
            try:
                body = json.loads(self.rfile.read(size) or b"{}")
                result = action(app, body if isinstance(body, dict) else {})
            # SystemExit and MailboxError carry the config's own advice.
            except (ValueError, SystemExit, MailboxError) as exc:
                self.send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            except Exception as exc:
                logger.exception("%s failed", self.path)
                self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"That failed ({exc})."})
                return
            self.send_json(HTTPStatus.OK, result)

    return Handler


class Server(ThreadingHTTPServer):
    # On Windows SO_REUSEADDR lets a second server bind a port the first still listens
    # on, so a second setup page would silently take over the first one's address.
    allow_reuse_address = sys.platform != "win32"


def serve(config_path: str | Path, port: int, *, open_browser: bool = True) -> None:
    app = App(config_path)
    try:
        server = Server(("127.0.0.1", port), handler(app, port))
    except OSError:
        logger.info("Port %d is taken, so it's using a free one instead", port)
        server = Server(("127.0.0.1", 0), handler(app, 0))
        port = server.server_address[1]
        server.RequestHandlerClass = handler(app, port)
    url = f"http://127.0.0.1:{port}/"
    print(f"The setup page is at {url} (Ctrl-C stops it)")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.stop()
        server.server_close()
