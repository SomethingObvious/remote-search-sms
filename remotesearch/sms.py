"""The Twilio transport: texts to the Twilio number in, SMS replies out, polled with no webhook."""

import logging
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any

import requests
from twilio.base.exceptions import TwilioException

from .config import phone_number, sender_permitted
from .net import REQUEST_TIMEOUT
from .reply import Incoming
from .state import Seen, State
from .text import clean_query

logger = logging.getLogger("remotesearch")

# Twilio's DateSent filter only takes a date, so the window reaches a day behind the
# last check.
LOOKBACK = timedelta(days=1)
# Twilio acts on these itself on North American numbers, and after STOP it refuses to
# text that phone at all, so answering them would only log a failed send.
OPT_OUT_WORDS = frozenset(
    "STOP STOPALL UNSUBSCRIBE CANCEL END QUIT START YES UNSTOP".split()  # noqa: SIM905
)


def twilio_client(config: dict[str, str]) -> Any:
    from twilio.http.http_client import TwilioHttpClient
    from twilio.rest import Client

    # Twilio's client waits forever by default, and one stuck call would stop the poll loop.
    return Client(
        config["TWILIO_ACCOUNT_SID"],
        config["TWILIO_AUTH_TOKEN"],
        http_client=TwilioHttpClient(timeout=REQUEST_TIMEOUT),
    )


def make_texter(client: Any, from_: str) -> Callable[[str, str], bool]:
    """Return ``text(to, body) -> delivered``, which only logs the text when there's no client."""

    def text(to: str, body: str) -> bool:
        if client is None:
            logger.info("Dry run, so not texting %s: %s", to or "PHONE_TO", body)
            return True
        try:
            sms = client.messages.create(to=to, from_=from_, body=body)
        except (TwilioException, requests.RequestException) as exc:
            logger.error("Couldn't text %s: %s", to, exc)
            return False
        logger.debug("Sent %s", sms.sid)
        return True

    return text


def _sent_at(message: Any) -> datetime:
    return message.date_sent or message.date_created or datetime.now(UTC)


class TwilioInbox:
    """Texts to the Twilio number that haven't been handled yet, oldest first."""

    def __init__(
        self,
        client: Any,
        number: str,
        state: State,
        text: Callable[[str, str], bool],
        *,
        allowed: set[str],
        catch_up: bool = False,
    ) -> None:
        self.client = client
        self.number = number
        self.state = state
        self.text = text
        self.allowed = allowed
        self.catch_up = catch_up

    def fetch(self) -> Iterator[Incoming]:
        seen = Seen(self.state, "twilio", LOOKBACK)
        listed = self.client.messages.list(to=self.number, date_sent_after=seen.since)
        inbound = (m for m in listed if m.direction == "inbound" and m.status == "received")
        fresh = seen.fresh(inbound, lambda m: m.sid, _sent_at, catch_up=self.catch_up)
        for message in fresh:
            seen.handled(message.sid, _sent_at(message))
            sender = phone_number(message.from_ or "") or ""
            if not sender_permitted(sender, self.allowed):
                logger.warning("Ignoring a text from %r as it isn't in ALLOWED_SENDERS", sender)
                continue
            query = clean_query(message.body or "")
            if not query:
                logger.warning("Text %s had nothing in it to answer", message.sid)
                continue
            if query.upper().rstrip(".!") in OPT_OUT_WORDS:
                logger.warning("Leaving %r from %s to Twilio's own opt-out handling", query, sender)
                continue
            yield Incoming(sender, query, "sms", partial(self.text, sender))
