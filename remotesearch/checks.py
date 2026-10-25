"""Trying each account and key in a config without answering anything."""

import imaplib
import smtplib
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

from . import ai
from .config import MAIL_KEYS, TWILIO_KEYS, sender_list
from .mail import LoginError, Mailbox, MailboxError, search_terms, servers, smtp_login, split_host
from .net import SourceError
from .reply import Answerer
from .search import brave


@dataclass(frozen=True)
class Result:
    name: str
    ok: bool | None  # None when it isn't set up, so there was nothing to try
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def senders(config: dict[str, str]) -> Result:
    allowed = sender_list(config)
    if not allowed:
        return Result("Allowed senders", False, "Nobody is allowed yet, so nothing gets answered.")
    return Result("Allowed senders", True, ", ".join(sorted(allowed)))


def twilio(config: dict[str, str]) -> Result:
    if not any(config.get(k) for k in TWILIO_KEYS):
        return Result("Twilio", None, "Not set up.")
    missing = [k for k in TWILIO_KEYS if not config.get(k)]
    if missing:
        return Result(
            "Twilio",
            False,
            f"{' and '.join(missing)} still {'needs' if len(missing) == 1 else 'need'} filling in.",
        )
    from twilio.base.exceptions import TwilioException

    from .sms import twilio_client

    number = config.get("TWILIO_PHONE_FROM", "")
    try:
        owned = twilio_client(config).incoming_phone_numbers.list(phone_number=number, limit=1)
    except (TwilioException, OSError) as exc:
        return Result("Twilio", False, f"Twilio turned down the SID and token ({exc}).")
    if not owned:
        return Result("Twilio", False, f"The account has no number {number or '(none set)'}.")
    return Result("Twilio", True, f"Logged in, and {number} belongs to the account.")


def mail(config: dict[str, str]) -> Result:
    if not any(config.get(k) for k in MAIL_KEYS):
        return Result("Email", None, "Not set up.")
    imap, smtp = servers(config)
    address, password = config.get("MAIL_ADDRESS", ""), config.get("MAIL_PASSWORD", "")
    box = Mailbox(address, password, imap, config.get("MAIL_FOLDER") or "INBOX")
    try:
        box.open()  # unseen() doesn't connect when no email address is allowed
        waiting = len(box.unseen(search_terms(sender_list(config))))
    except LoginError as exc:
        return Result("Email", False, f"{exc}. It needs an app password, not the normal one.")
    except (MailboxError, imaplib.IMAP4.error, OSError) as exc:
        return Result("Email", False, f"Couldn't read {box.folder} on {box.host} ({exc}).")
    finally:
        box.close()
    host = split_host(smtp, 465)[0]
    try:
        smtp_login(smtp, address, password).close()
    except (smtplib.SMTPException, OSError) as exc:
        return Result("Email", False, f"Reading works, but {host} won't send ({exc}).")
    return Result("Email", True, f"Reading {box.folder} and sending work. {waiting} waiting now.")


def model(config: dict[str, str]) -> Result:
    found = ai.model_from_config(config)
    if not found:
        return Result("AI answers", None, "Not set up.")
    said = ai.ask(found, "Reply with the word ready.", "Are you there?")
    if not said:
        return Result(
            "AI answers", False, f"{found.name} at {found.url} didn't answer. See the log."
        )
    return Result("AI answers", True, f"{found.name} at {found.provider} answered.")


def brave_search(config: dict[str, str]) -> Result:
    key = config.get("BRAVE_API_KEY", "")
    if not key:
        return Result("Brave Search", None, "Not set up, so the free engines are used.")
    try:
        hits = brave("tide tables", key)
    except SourceError as exc:
        return Result("Brave Search", False, f"Brave Search failed ({exc.why}).")
    return Result("Brave Search", True, f"Answered with {len(hits)} results.")


def commands(config: dict[str, str]) -> Result:
    table = Answerer(config).commands
    names = dict.fromkeys(c.words[0] for c in table.values())
    return Result("Commands", True, f"{len(names)} on: {' '.join(names)}")


TEST_TEXT = "Remote search can text you. Text help to see the commands."


def send_test_text(config: dict[str, str]) -> str:
    """Text PHONE_TO through Twilio, returning the number, or raise ValueError saying why not."""
    from .sms import make_texter, twilio_client

    phone = config.get("PHONE_TO", "")
    if not phone:
        raise ValueError("PHONE_TO isn't set, so there's nowhere to send a test text.")
    if not all(config.get(k) for k in TWILIO_KEYS):
        raise ValueError("Twilio isn't set up, so there's nothing to send a test text with.")
    texter = make_texter(twilio_client(config), config["TWILIO_PHONE_FROM"])
    if not texter(phone, TEST_TEXT):
        raise ValueError(f"Twilio didn't send the test text to {phone}. The log says why.")
    return phone


# Named here as well as in each Result, so a check that stops on a bad setting keeps its name.
CHECKS: tuple[tuple[str, Callable[[dict[str, str]], Result]], ...] = (
    ("Allowed senders", senders),
    ("Twilio", twilio),
    ("Email", mail),
    ("AI answers", model),
    ("Brave Search", brave_search),
    ("Commands", commands),
)


def run_all(config: dict[str, str]) -> list[Result]:
    """Every check, with a mistake in the config turned into a failed result."""
    results = []
    for name, check in CHECKS:
        try:
            results.append(check(config))
        except SystemExit as exc:  # the config's own explanation of what to change
            results.append(Result(name, False, str(exc)))
    return results
