"""A small JSON file that survives restarts: texts already handled and unsent pages."""

import json
import logging
import os
import sys
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, TypeVar

logger = logging.getLogger("remotesearch")
T = TypeVar("T")

DEFAULT_STATE_FILE = "remote-search-state.json"


def hold(path: Path) -> int:
    """Lock a file beside the state file, returning the descriptor that holds it.

    Each poller keeps the file in memory and writes all of it, so two at once answer
    every text twice and lose each other's hourly counts.
    """
    lock = path.with_name(path.name + ".lock")
    fd = os.open(lock, os.O_RDWR | os.O_CREAT)
    try:
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        raise SystemExit(
            f"Another remote-search is already answering with {path}. Stop that one "
            "first, as two at once would answer every text twice."
        ) from None
    return fd


class State:
    def __init__(self, path: Path, *, dry_run: bool = False) -> None:
        self.path = path
        # A dry run reads the file so it knows what's been answered, but what it
        # handles itself is only kept in memory, and the file is left as it was.
        self.dry_run = dry_run
        self.data: dict[str, Any] = {}
        if path.exists():
            try:
                self.data = json.loads(path.read_text(encoding="utf-8"))
            except (ValueError, OSError) as exc:
                raise SystemExit(
                    f"Couldn't read {path} ({exc}). Delete it to start fresh, though texts "
                    "from the last day or so may then get answered a second time."
                ) from None

    def save(self) -> None:
        if self.dry_run:
            return
        # Written beside the real file and swapped in, so a crash mid-write can't
        # leave half a file that fails to load on the next start.
        temp = self.path.with_name(self.path.name + ".tmp")
        temp.write_text(json.dumps(self.data, indent=1), encoding="utf-8")
        temp.replace(self.path)

    def set_pages(self, sender: str, pages: list[str]) -> None:
        stored = self.data.setdefault("pages", {})
        if pages:
            stored[sender] = pages
        elif stored.pop(sender, None) is None:
            return  # this sender had no pages, so the file stays as it is
        self.save()

    def next_page(self, sender: str) -> str | None:
        pages = self.data.get("pages", {}).get(sender) or []
        if not pages:
            return None
        self.set_pages(sender, pages[1:])
        return str(pages[0])


class Seen:
    """The texts one inbox has handled, by key, kept under ``name`` in the state file.

    Neither Twilio nor the phone can list only what's new, so each poll lists a window
    reaching ``lookback`` behind the last one, and the keys here keep the overlap from
    being answered twice.
    """

    def __init__(self, state: State, name: str, lookback: timedelta) -> None:
        self.state = state
        self.name = name
        self.lookback = lookback
        self.now = datetime.now(UTC)
        # Only stored once a listing works, so a failed first poll is still a first poll.
        self.first_run = name not in state.data
        self.box = state.data.get(name) or {"seen": {}, "checked": self.now.isoformat()}
        self.since = datetime.fromisoformat(self.box["checked"]) - lookback

    def fresh(
        self,
        listed: Iterable[T],
        key: Callable[[T], str],
        when: Callable[[T], datetime],
        *,
        catch_up: bool,
    ) -> list[T]:
        """What in ``listed`` isn't handled yet, oldest first.

        On the first start that's all set aside as handled, unless ``catch_up``.
        """
        new = sorted((m for m in listed if key(m) not in self.box["seen"]), key=when)
        # Keys older than the window can't come back, so they're dropped to keep the file small.
        oldest = self.since - self.lookback
        self.box["seen"] = {
            k: at for k, at in self.box["seen"].items() if datetime.fromisoformat(at) >= oldest
        }
        self.box["checked"] = self.now.isoformat()
        self.state.data[self.name] = self.box
        if self.first_run and not catch_up:
            for message in new:
                self.box["seen"][key(message)] = when(message).isoformat()
            if new:
                logger.info("Skipping %d texts from before the first start", len(new))
            new = []
        self.state.save()
        return new

    def handled(self, key: str, when: datetime) -> None:
        # Saved before the reply goes out, so a crash or a failed send can't turn into
        # the same answer on every poll.
        self.box["seen"][key] = when.isoformat()
        self.state.save()
