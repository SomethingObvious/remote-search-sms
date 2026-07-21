"""Checking every inbox on a timer and within the reply caps, for the command and the page."""

import logging
import os
import threading
import time
from collections import deque
from collections.abc import Iterable
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any, Protocol

from .config import (
    MAIL_KEYS,
    TWILIO_KEYS,
    beside_config,
    phone_number,
    require,
    sender_list,
    setting,
)
from .mail import LoginError, Mailbox, MailInbox, servers
from .reply import DEFAULT_SMS_CHARS, ONLINE_TEXT, Answerer, Incoming, Responder
from .sms import TwilioInbox, make_texter, twilio_client
from .state import DEFAULT_STATE_FILE, State, hold

logger = logging.getLogger("remotesearch")

TWILIO_MAX_CHARS = 1600  # Twilio rejects a longer body outright
DEFAULT_MAX_REPLIES = 10  # per poll, and every reply is a billed SMS
DEFAULT_INTERVAL = 5
MOST_BACKOFF = 300


class Inbox(Protocol):
    def fetch(self) -> Iterable[Incoming]: ...


class Poller:
    """The inboxes a config sets up, and the loop that answers them.

    Building one checks the config, so a mistake stops it here with a SystemExit
    that says what to change, before anything is polled.
    """

    def __init__(
        self,
        config: dict[str, str],
        *,
        source: str | Path = "config.txt",
        dry_run: bool = False,
        catch_up: bool = False,
    ) -> None:
        self.config = config
        self.dry_run = dry_run
        self.limit = setting(config, "MAX_SMS_CHARS", DEFAULT_SMS_CHARS)
        if self.limit > TWILIO_MAX_CHARS:
            raise SystemExit(
                f"MAX_SMS_CHARS is {self.limit}, but Twilio won't send more than "
                f"{TWILIO_MAX_CHARS} characters in one text."
            )
        self.allowed = sender_list(config)
        if not self.allowed:
            raise SystemExit(
                "ALLOWED_SENDERS is empty, so anyone who found the address or number would get "
                "replies. Set it to your +phone number, your carrier's gateway domain or the "
                "email addresses that can ask."
            )
        use_mail = any(config.get(k) for k in MAIL_KEYS)
        use_twilio = any(s.startswith("+") for s in self.allowed)
        if not use_mail and not use_twilio:
            raise SystemExit(
                f"{source} doesn't set up anything to watch. Set MAIL_ADDRESS and MAIL_PASSWORD "
                "to read email, or put your +phone number in ALLOWED_SENDERS and fill in the "
                "Twilio settings to answer texts."
            )
        require(
            config, (MAIL_KEYS if use_mail else ()) + (TWILIO_KEYS if use_twilio else ()), source
        )
        self.interval = setting(config, "POLL_INTERVAL", DEFAULT_INTERVAL)
        self.max_replies = setting(config, "MAX_REPLIES_PER_POLL", DEFAULT_MAX_REPLIES)
        self.state = State(beside_config(config, "STATE_FILE", DEFAULT_STATE_FILE), dry_run=dry_run)
        self.responder = Responder(Answerer(config, self.limit), self.state)
        self.recent: deque[dict[str, Any]] = deque(maxlen=50)
        self.inboxes: list[Inbox] = []
        self._closers: list[Any] = []

        client = twilio_client(config) if all(config.get(k) for k in TWILIO_KEYS) else None
        text = make_texter(None if dry_run else client, config.get("TWILIO_PHONE_FROM", ""))
        phone = config.get("PHONE_TO", "")
        text_phone = partial(text, phone) if client and phone else None
        # Someone texting by email gateway can't see the log, so a text says it's up.
        self.announce = text_phone if use_mail else None
        if use_mail:
            self.inboxes.append(self._mail_inbox(text_phone, catch_up))
        if use_twilio:
            logger.info("Checking texts to %s", config["TWILIO_PHONE_FROM"])
            self.inboxes.append(
                TwilioInbox(
                    client,
                    config["TWILIO_PHONE_FROM"],
                    self.state,
                    text,
                    allowed=self.allowed,
                    catch_up=catch_up,
                )
            )
        logger.info("Answering %s only", ", ".join(sorted(self.allowed)))
        # Dry runs write nothing, so one can run beside the real thing.
        self._lock = None if dry_run else hold(self.state.path)

    def _mail_inbox(self, text_phone: Any, catch_up: bool) -> MailInbox:
        imap, smtp = servers(self.config)
        box = Mailbox(
            self.config["MAIL_ADDRESS"],
            self.config["MAIL_PASSWORD"],
            imap,
            self.config.get("MAIL_FOLDER") or "INBOX",
        )
        self._closers.append(box)
        inbox = MailInbox(
            box,
            smtp,
            allowed=self.allowed,
            sms_senders=sender_list(self.config, "REPLY_BY_SMS"),
            text_phone=text_phone,
            phone=phone_number(self.config.get("PHONE_TO", "")) or "",
            dry_run=self.dry_run,
        )
        if not catch_up:
            waiting = inbox.skip_waiting()
            if waiting:
                logger.info("Leaving %d unread messages from before the start alone", waiting)
        logger.info("Checking %s in %s", box.folder, box.address)
        return inbox

    def poll(self) -> int:
        """Answer what's waiting in every inbox, up to the caps, and return how many went out.

        The cap is checked before the next message is taken, so whatever's left stays
        unread or unseen, and gets its answer on a later poll.
        """
        replied = 0
        for inbox in self.inboxes:
            for incoming in inbox.fetch():
                replied += self.answer(incoming)
                if replied >= self.max_replies:
                    logger.warning(
                        "Sent %d replies this poll, which is the cap, so the rest wait",
                        self.max_replies,
                    )
                    return replied
        return replied

    def answer(self, incoming: Incoming) -> bool:
        """Reply to one question, returning whether the reply went out."""
        try:
            logger.info("Question from %s: %s", incoming.sender or "someone", incoming.text)
            asker = incoming.replies_to or incoming.sender
            reply = self.responder.reply(asker, incoming.text, incoming.channel)
            sent = incoming.send(reply)
        except Exception as exc:  # one bad question shouldn't hold up the rest
            logger.error("Couldn't answer %s: %s", incoming.sender, exc)
            return False
        self.recent.appendleft(
            {
                "at": datetime.now(UTC).isoformat(timespec="seconds"),
                "sender": incoming.sender,
                "question": incoming.text,
                "reply": reply,
                "sent": sent,
            }
        )
        return sent

    def run(self, stop: threading.Event | None = None) -> None:
        """Poll until ``stop`` is set, backing off while polls keep failing."""
        stop = stop or threading.Event()
        if self.announce:
            self.announce(ONLINE_TEXT)
        failures = 0
        while not stop.is_set():
            try:
                self.poll()
                failures = 0
            except LoginError:
                raise  # retrying can't fix a changed password
            except Exception as exc:  # mail servers and Twilio have short outages
                failures += 1
                logger.error("Poll failed (%d in a row): %s", failures, exc)
            pause(stop, max(self.interval, min(self.interval * 2**failures, MOST_BACKOFF)))

    def close(self) -> None:
        for closer in self._closers:
            closer.close()
        if self._lock is not None:
            os.close(self._lock)
            self._lock = None


def pause(stop: threading.Event, seconds: float) -> None:
    """Wait until ``stop`` is set or ``seconds`` pass."""
    # A long Event.wait can't be interrupted by Ctrl-C on Windows, so it waits a second
    # at a time.
    end = time.monotonic() + seconds
    while not stop.is_set() and (left := end - time.monotonic()) > 0:
        stop.wait(min(left, 1))
