"""An Android phone with a SIM as the texting gateway, through SMS Gateway for Android.

That app (https://sms-gate.app) serves a small API on the phone's address on the home
network. Texts in are listed from its /inbox, so there's no webhook here either.
"""

import logging
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

import requests

from .config import phone_number, sender_permitted
from .net import REQUEST_TIMEOUT
from .reply import Incoming
from .state import Seen, State
from .text import clean_query

logger = logging.getLogger("remotesearch")

# The inbox lists texts by when the phone got them, and a day's overlap covers a
# phone that was off for a while.
LOOKBACK = timedelta(days=1)
MOST_LISTED = 500  # the most /inbox gives back at once
INBOX_VERSION = "1.59"  # the first version of the app with /inbox


class PhoneError(Exception):
    """The phone didn't answer, or turned the request down."""


class Phone:
    def __init__(self, url: str, user: str, password: str) -> None:
        self.url = url.strip().rstrip("/")
        if "://" not in self.url:
            self.url = f"http://{self.url}"
        self.auth = (user, password)

    def call(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = requests.request(
                method, self.url + path, auth=self.auth, timeout=REQUEST_TIMEOUT, **kwargs
            )
        except requests.RequestException as exc:
            raise PhoneError(
                f"The phone at {self.url} didn't answer. Check that SMS Gateway's local "
                f"server is on and the phone is on this network ({exc})"
            ) from None
        if response.status_code == 401:
            raise PhoneError(f"The phone at {self.url} turned down the username and password")
        if response.status_code == 404 and path.startswith("/inbox"):
            raise PhoneError(
                f"SMS Gateway on the phone can't list texts. Update it to {INBOX_VERSION} or newer"
            )
        if not response.ok:
            raise PhoneError(
                f"The phone at {self.url} answered {response.status_code} to {path}: "
                f"{response.text[:200]}"
            )
        try:
            return response.json() if response.content else None
        except ValueError:
            raise PhoneError(
                f"The phone at {self.url} sent back something that isn't JSON"
            ) from None

    def inbox(self, since: datetime, limit: int = MOST_LISTED) -> list[dict[str, Any]]:
        """Texts the phone got since ``since``, newest first."""
        listed = self.call(
            "GET",
            "/inbox",
            params={
                "type": "SMS",
                "from": since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "limit": limit,
            },
        )
        if not isinstance(listed, list):
            raise PhoneError(f"The phone's inbox came back as {type(listed).__name__}, not a list")
        return [m for m in listed if isinstance(m, dict)]

    def send(self, to: str, body: str) -> None:
        self.call("POST", "/messages", json={"textMessage": {"text": body}, "phoneNumbers": [to]})


def make_phone_texter(phone: Phone | None) -> Callable[[str, str], bool]:
    """Return ``text(to, body) -> sent``, which only logs the text when there's no phone."""

    def text(to: str, body: str) -> bool:
        if phone is None:
            logger.info("Dry run, so not texting %s: %s", to or "PHONE_TO", body)
            return True
        try:
            phone.send(to, body)
        except PhoneError as exc:
            logger.error("Couldn't text %s: %s", to, exc)
            return False
        return True

    return text


def _received(message: dict[str, Any]) -> datetime:
    try:
        return datetime.fromisoformat(str(message.get("createdAt"))).astimezone(UTC)
    except ValueError:
        return datetime.now(UTC)


def _key(message: dict[str, Any]) -> str:
    # The app's id is a 32-bit hash of the text, so the time and sender come along with it.
    return f"{message.get('id')}|{message.get('createdAt')}|{message.get('sender')}"


class PhoneInbox:
    """Texts to the Android phone that haven't been handled yet, oldest first."""

    def __init__(
        self,
        phone: Phone,
        state: State,
        text: Callable[[str, str], bool],
        *,
        allowed: set[str],
        catch_up: bool = False,
    ) -> None:
        self.phone = phone
        self.state = state
        self.text = text
        self.allowed = allowed
        self.catch_up = catch_up

    def fetch(self) -> Iterator[Incoming]:
        seen = Seen(self.state, "android", LOOKBACK)
        listed = self.phone.inbox(seen.since)
        if len(listed) >= MOST_LISTED:
            # A day of texts to one person's phone is nowhere near 500, so there's no paging.
            logger.warning(
                "The phone listed %d texts at once, so older ones may be missed", MOST_LISTED
            )
        for message in seen.fresh(listed, _key, _received, catch_up=self.catch_up):
            seen.handled(_key(message), _received(message))
            raw = str(message.get("sender") or "")
            sender = phone_number(raw) or ""
            if not sender_permitted(sender, self.allowed):
                logger.warning("Ignoring a text from %r as it isn't in ALLOWED_SENDERS", raw)
                continue
            query = clean_query(str(message.get("contentPreview") or ""))
            if not query:
                logger.warning("A text from %s had nothing in it to answer", sender)
                continue
            yield Incoming(sender, query, "sms", partial(self.text, sender))
