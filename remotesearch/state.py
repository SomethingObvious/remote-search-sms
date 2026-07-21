"""A small JSON file that survives restarts: Twilio messages already handled and unsent pages."""

import json
import os
import sys
from pathlib import Path
from typing import Any

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
