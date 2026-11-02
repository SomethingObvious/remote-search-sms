import io
import json
import os
import threading
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from remotesearch.ui import App, cost, handler, page

PORT = 8765


class FakeSocket:
    """Just enough of a socket for the request handler, which reads a request and writes a reply."""

    def __init__(self, request: bytes) -> None:
        self.request = io.BytesIO(request)
        self.sent = b""

    def makefile(self, mode: str, *args: Any) -> io.BytesIO:
        return self.request

    def sendall(self, data: bytes) -> None:
        self.sent += data


def call(
    app: App,
    path: str,
    body: Any = None,
    *,
    method: str = "POST",
    host: str = f"127.0.0.1:{PORT}",
    token: str | None = None,
) -> tuple[int, bytes]:
    data = json.dumps(body or {}).encode()
    headers = [f"Host: {host}", f"Content-Length: {len(data)}"]
    headers.append(f"X-Token: {app.token if token is None else token}")
    raw = f"{method} {path} HTTP/1.1\r\n" + "\r\n".join(headers) + "\r\n\r\n"
    sock = FakeSocket(raw.encode() + (data if method == "POST" else b""))
    handler(app, PORT)(sock, ("127.0.0.1", 50000), None)  # type: ignore[arg-type]
    head, _, payload = sock.sent.partition(b"\r\n\r\n")
    return int(head.split()[1]), payload


@pytest.fixture
def app(tmp_path: Path) -> App:
    config = tmp_path / "config.txt"
    config.write_text(
        "# Who can ask\nALLOWED_SENDERS=+17785551234\nMAIL_PASSWORD=abcd efgh ijkl mnop\n"
        f"STATE_FILE={tmp_path / 'state.json'}\n",
        encoding="utf-8",
    )
    return App(config)


def test_page_carries_the_token(app: App) -> None:
    html = page(app.token).decode()
    assert f'<meta name="token" content="{app.token}">' in html
    assert "{{TOKEN}}" not in html


def test_other_hosts_are_refused(app: App) -> None:
    # A rebound DNS name reaches 127.0.0.1 with its own name in Host.
    status, _ = call(app, "/", method="GET", host=f"evil.example:{PORT}")
    assert status == 403
    status, _ = call(app, "/api/state", host=f"evil.example:{PORT}")
    assert status == 403


def test_the_page_and_fonts_are_served(app: App) -> None:
    status, body = call(app, "/", method="GET")
    assert status == 200
    assert app.token.encode() in body
    status, body = call(app, "/static/barlow-condensed-700.woff2", method="GET")
    assert status == 200
    assert body.startswith(b"wOF2")
    status, _ = call(app, "/static/../ui.py", method="GET")
    assert status == 404


def test_posts_need_the_token(app: App) -> None:
    status, body = call(app, "/api/state", token="wrong")
    assert status == 403
    assert json.loads(body) == {"error": "Reload the page to get a new key."}
    status, _ = call(app, "/api/state", token="")
    assert status == 403


def test_saved_secrets_never_go_back(app: App) -> None:
    status, body = call(app, "/api/state")
    assert status == 200
    fields = {f["key"]: f for f in json.loads(body)["settings"]}
    assert fields["MAIL_PASSWORD"]["value"] == ""
    assert fields["MAIL_PASSWORD"]["saved"] is True
    assert fields["ALLOWED_SENDERS"]["value"] == "+17785551234"
    assert b"abcd efgh" not in body


def test_save_keeps_comments_and_writes_the_change(app: App) -> None:
    status, _ = call(app, "/api/save", {"changes": {"PHONE_TO": " +17785551234 "}})
    assert status == 200
    text = app.path.read_text(encoding="utf-8")
    assert text.startswith("# Who can ask\nALLOWED_SENDERS=+17785551234\n")
    assert text.endswith("PHONE_TO=+17785551234\n")


def test_save_refuses_a_line_break(app: App) -> None:
    before = app.path.read_text(encoding="utf-8")
    status, body = call(app, "/api/save", {"changes": {"PHONE_TO": "+1\nMAIL_HOST=evil"}})
    assert status == 400
    assert "line break" in json.loads(body)["error"]
    assert app.path.read_text(encoding="utf-8") == before


def test_save_refuses_an_unknown_key(app: App) -> None:
    status, body = call(app, "/api/save", {"changes": {"PATH": "x"}})
    assert status == 400
    assert json.loads(body) == {"error": "PATH isn't a setting this knows."}


def test_ask_returns_pages_with_their_cost(app: App) -> None:
    status, body = call(app, "/api/ask", {"question": "calc 2+2"})
    assert status == 200
    assert json.loads(body) == {
        "limit": 300,
        "pages": [{"text": "calc: 2+2 = 4", "chars": 13, "segments": 1}],
    }


def test_a_bad_setting_comes_back_as_its_advice(app: App) -> None:
    call(app, "/api/save", {"changes": {"MAX_SMS_CHARS": "lots"}})
    status, body = call(app, "/api/ask", {"question": "calc 2+2"})
    assert status == 400
    assert (
        json.loads(body)["error"]
        == "MAX_SMS_CHARS has to be a whole number of 1 or more, not 'lots'."
    )


def test_commands_show_whether_they_are_on(app: App) -> None:
    call(app, "/api/save", {"changes": {"COMMANDS_OFF": "tide"}})
    _, body = call(app, "/api/state")
    on = {c["words"][0]: c["on"] for c in json.loads(body)["commands"]}
    assert on["tide"] is False
    assert on["weather"] is True


def test_unknown_paths_say_so(app: App) -> None:
    status, body = call(app, "/api/nothing")
    assert status == 404
    assert json.loads(body) == {"error": "There's nothing at /api/nothing."}


def test_plugins_are_listed_before_anything_is_asked(app: App, tmp_path: Path) -> None:
    folder = tmp_path / "plugins"
    folder.mkdir()
    (folder / "campsite.py").write_text(
        "from remotesearch.commands import command\n\n\n"
        '@command("campsite", help="campsite: the booking line.", bare=True, listed=False)\n'
        "def campsite(_text: str) -> str:\n"
        '    return "site 14"\n',
        encoding="utf-8",
    )
    call(app, "/api/save", {"changes": {"PLUGINS_DIR": str(folder)}})
    _, body = call(app, "/api/state")
    found = [c for c in json.loads(body)["commands"] if c["words"] == ["campsite"]]
    assert found == [
        {"words": ["campsite"], "help": "campsite: the booking line.", "on": True, "plugin": True}
    ]


def test_activity_shows_what_each_answer_cost() -> None:
    row = {"sender": "+16045551234", "question": "tide Tofino", "reply": "tide: " + "x" * 200}
    assert cost(row) == row | {"chars": 206, "segments": 2}
    assert cost(row | {"reply": ""})["segments"] == 0


def plugin(folder: Path, word: str) -> None:
    folder.mkdir()
    (folder / "mine.py").write_text(
        "from remotesearch.commands import command\n\n\n"
        f'@command("{word}", help="{word}: a test.", bare=True, listed=False)\n'
        f"def {word}(_text: str) -> str:\n"
        f'    return "{word}"\n',
        encoding="utf-8",
    )


def test_only_the_current_plugin_folder_counts(app: App, tmp_path: Path) -> None:
    plugin(tmp_path / "old", "oldword")
    plugin(tmp_path / "new", "newword")  # the same file name in another folder
    words = []
    for folder in ("old", "new"):
        call(app, "/api/save", {"changes": {"PLUGINS_DIR": str(tmp_path / folder)}})
        _, body = call(app, "/api/state")
        words.append({c["words"][0] for c in json.loads(body)["commands"] if c["plugin"]})
    assert words == [{"oldword"}, {"newword"}]


def test_a_setting_from_the_environment_is_marked(app: App) -> None:
    with patch.dict(os.environ, {"PHONE_TO": "+16045559999"}):
        _, body = call(app, "/api/state")
    field = next(f for f in json.loads(body)["settings"] if f["key"] == "PHONE_TO")
    assert (field["value"], field["fromEnv"]) == ("+16045559999", True)


def test_save_doesnt_wait_for_start_to_connect(app: App) -> None:
    connecting, release = threading.Event(), threading.Event()

    def slow_poller(*_args: Any, **_kwargs: Any) -> Any:
        connecting.set()
        release.wait(5)
        raise SystemExit("stopped here")

    with patch("remotesearch.ui.Poller", slow_poller):
        starting = threading.Thread(target=lambda: call(app, "/api/start"))
        starting.start()
        assert connecting.wait(5)
        status, body = call(app, "/api/save", {"changes": {"PHONE_TO": "+16045551234"}})
        assert status == 200
        assert json.loads(body)["restartNeeded"] is False  # nothing is running yet
        assert app.saved_while_running is True
        release.set()
        starting.join(5)
    assert app.starting is False
