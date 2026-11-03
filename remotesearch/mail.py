"""Mail in over IMAP and answers out over SMTP, logged in with an app password."""

import base64
import contextlib
import email
import imaplib
import logging
import re
import smtplib
import ssl
from collections.abc import Callable, Iterator
from email.message import EmailMessage, Message
from email.policy import default as email_policy
from email.utils import parseaddr

from bs4 import BeautifulSoup

from .config import sender_permitted
from .net import REQUEST_TIMEOUT
from .reply import Incoming
from .text import clean_query

logger = logging.getLogger("remotesearch")

# Proton Bridge, the app Proton needs for IMAP, serves the mailbox on this computer.
PROTON_BRIDGE = ("127.0.0.1:1143", "127.0.0.1:1025")
# The servers for the mailboxes that still take an app password.
KNOWN_SERVERS = {
    "gmail.com": ("imap.gmail.com", "smtp.gmail.com:465"),
    "googlemail.com": ("imap.gmail.com", "smtp.gmail.com:465"),
    "icloud.com": ("imap.mail.me.com", "smtp.mail.me.com:587"),
    "me.com": ("imap.mail.me.com", "smtp.mail.me.com:587"),
    "mac.com": ("imap.mail.me.com", "smtp.mail.me.com:587"),
    "fastmail.com": ("imap.fastmail.com", "smtp.fastmail.com:465"),
    "yahoo.com": ("imap.mail.yahoo.com", "smtp.mail.yahoo.com:465"),
    "ymail.com": ("imap.mail.yahoo.com", "smtp.mail.yahoo.com:465"),
    "aol.com": ("imap.aol.com", "smtp.aol.com:465"),
    "proton.me": PROTON_BRIDGE,
    "protonmail.com": PROTON_BRIDGE,
    "protonmail.ch": PROTON_BRIDGE,
    "pm.me": PROTON_BRIDGE,
}
# Microsoft turned off password logins to IMAP for Outlook.com in 2024 and for
# Microsoft 365 before that, so these only take OAuth now.
MICROSOFT = re.compile(
    r"(^|\.)(outlook|hotmail|live|msn)\.[a-z]{2,3}(\.[a-z]{2})?$|(^|\.)office365\.com$"
)
OUTLOOK_HELP = (
    "Microsoft only lets {name} into IMAP with an OAuth sign-in now, which this doesn't do. "
    "Read a Gmail, iCloud, Fastmail, Yahoo or Proton address instead, and your Outlook "
    "address can still email questions to it from ALLOWED_SENDERS."
)
LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})
# A carrier's SMS-to-email gateway sends from the phone's own number, like
# 6045551234@txt.bell.ca, so mail from an address like that gets its answer by SMS.
PHONE_GATEWAY = re.compile(r"\+?1?\d{10}")
NO_REPLY = re.compile(r"no[._-]?reply|do[._-]?not[._-]?reply|mailer-daemon|postmaster")


class MailboxError(Exception):
    """Something about the mailbox that a retry won't change."""


class LoginError(MailboxError):
    """The mail server turned down the address and password."""


class FolderError(MailboxError):
    """The folder or label in MAIL_FOLDER isn't there."""


# Without a context of their own, imaplib and smtplib check neither the certificate nor
# the host name, so anyone on the network path could take the password.
TLS = ssl.create_default_context()
# Proton Bridge signs its own certificate. Nobody on the network can get between two
# programs on the same computer, so a server on this one isn't checked.
LOCAL_TLS = ssl.create_default_context()
LOCAL_TLS.check_hostname = False
LOCAL_TLS.verify_mode = ssl.CERT_NONE
# A refused login with one of these in it is the server being busy, not a wrong password.
BUSY = re.compile(r"UNAVAILABLE|INUSE|THROTTLED|too many|try again", re.IGNORECASE)
# Tries at reading or marking one message before it's left alone.
MOST_TRIES = 3


def servers(config: dict[str, str]) -> tuple[str, str]:
    """The IMAP and SMTP servers for MAIL_ADDRESS, as host or host:port."""
    domain = config.get("MAIL_ADDRESS", "").rpartition("@")[2].lower()
    imap, smtp = KNOWN_SERVERS.get(domain, ("", ""))
    imap, smtp = config.get("IMAP_HOST") or imap, config.get("SMTP_HOST") or smtp
    host = split_host(imap.lower(), 993)[0]
    # An Outlook address with an IMAP_HOST of its own, like a local gateway that does the
    # OAuth, is left to work.
    if MICROSOFT.search(host) or (not config.get("IMAP_HOST") and MICROSOFT.search(domain)):
        raise SystemExit(OUTLOOK_HELP.format(name=host or domain))
    if not imap or not smtp:
        raise SystemExit(
            f"There are no known mail servers for {domain or 'MAIL_ADDRESS'}. Set IMAP_HOST "
            "and SMTP_HOST to your provider's, like imap.example.com and smtp.example.com:587."
        )
    return imap, smtp


def imap_utf7(text: str) -> str:
    """A folder name in the modified UTF-7 that IMAP uses, as in R&AOk-ponses."""
    out: list[str] = []
    wide: list[str] = []

    def flush() -> None:
        if wide:
            packed = base64.b64encode("".join(wide).encode("utf-16-be")).decode()
            out.append("&" + packed.rstrip("=").replace("/", ",") + "-")
            wide.clear()

    for char in text:
        if " " <= char <= "~":
            flush()
            out.append("&-" if char == "&" else char)
        else:
            wide.append(char)
    flush()
    return "".join(out)


def split_host(text: str, port: int) -> tuple[str, int]:
    host, _, given = text.partition(":")
    return host, int(given) if given.isdecimal() else port


def tls_for(host: str) -> ssl.SSLContext:
    return LOCAL_TLS if host.lower() in LOOPBACK else TLS


def sender_address(message: Message) -> str:
    """The From address, lowercased, or an empty string when there isn't one clear address."""
    # parseaddr takes the address in the angle brackets. A plain regex takes the first
    # thing shaped like an address, which can be a fake one in the display name.
    try:
        address = parseaddr(str(message.get("From", "")))[1].lower()
    except (IndexError, ValueError, TypeError, AttributeError):
        # The strict header parser throws on some broken From lines, like (x):a@b.c.
        return ""
    return address if "@" in address else ""


# Where a reply's own text ends: the quoted message under it, or a signature.
QUOTED = re.compile(
    r"^(>|-- ?$|_{5,}|-{3,} ?Original Message|From: |Sent from |On [^\n]*(\n[^\n]*)?wrote:\s*$)",
    re.MULTILINE,
)


def own_text(body: str) -> str:
    """The part of a message body above any quoted reply or signature."""
    found = QUOTED.search(body)
    return body[: found.start()] if found else body


def extract_query(message: Message) -> str | None:
    """The question in a message, from its plain text if it has some and its HTML if not."""
    if not isinstance(message, EmailMessage):
        return None
    for kind in ("plain", "html"):
        part = message.get_body(preferencelist=(kind,))
        if part is None:
            continue
        try:
            body = part.get_content()
        except (LookupError, ValueError):  # a charset Python doesn't know, or a broken body
            body = part.get_payload(decode=True).decode("utf-8", "replace")  # type: ignore[union-attr]
        if kind == "html":
            body = BeautifulSoup(body, "html.parser").get_text("\n")
        text = clean_query(own_text(body))
        if text:
            return text
    return None


def reply_to(message: Message, text: str, address: str) -> EmailMessage:
    """An answer in the same thread as ``message``."""
    reply = EmailMessage()
    reply["From"] = address
    reply["To"] = sender_address(message)
    subject = re.sub(r"\s+", " ", str(message.get("Subject", ""))).strip() or "your question"
    reply["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    message_id = str(message.get("Message-ID", "")).strip()
    if message_id:
        reply["In-Reply-To"] = message_id
        reply["References"] = message_id
    reply.set_content(text)
    return reply


def search_terms(allowed: set[str]) -> list[str]:
    """IMAP search keys for unread mail from any allowed address or domain.

    Asking the server to filter means mail from anyone else is never fetched or touched,
    so this can read a mailbox that's also used for other things.
    """
    sources = sorted(a.replace('"', "").replace("\\", "") for a in allowed if "+" not in a[:1])
    odd = [s for s in sources if not s.isascii()]
    if odd:
        raise SystemExit(
            f"IMAP can't search for {odd[0]}, as it isn't plain ASCII. Use the address "
            "the provider gives for it in ASCII form, the one starting with xn-- for a domain."
        )
    if not sources:
        return []
    terms = [f'FROM "{source}"' for source in sources]
    while len(terms) > 1:
        terms = [f"OR {terms[0]} {terms[1]}", *terms[2:]]
    return ["UNSEEN", terms[0]]


class Mailbox:
    """One IMAP connection to one folder, opened when first needed and again after a drop."""

    def __init__(self, address: str, password: str, host: str, folder: str = "INBOX") -> None:
        self.address = address
        self.password = password
        self.host, self.port = split_host(host, 993)
        self.folder = folder or "INBOX"
        self.conn: imaplib.IMAP4 | None = None

    def open(self) -> imaplib.IMAP4:
        if self.conn:
            return self.conn
        context = tls_for(self.host)
        conn: imaplib.IMAP4
        if self.port == 993:
            conn = imaplib.IMAP4_SSL(
                self.host, self.port, ssl_context=context, timeout=REQUEST_TIMEOUT
            )
        else:  # like Proton Bridge on 1143, which starts plain and upgrades
            conn = imaplib.IMAP4(self.host, self.port, timeout=REQUEST_TIMEOUT)
            try:
                conn.starttls(context)
            except BaseException:
                with contextlib.suppress(OSError):  # a failed handshake can leave no socket
                    conn.shutdown()
                raise
        try:
            conn.login(self.address, self.password)
        except imaplib.IMAP4.abort:
            raise  # the connection dropped mid-login, which the next poll retries
        except imaplib.IMAP4.error as exc:
            with contextlib.suppress(OSError):
                conn.shutdown()
            if BUSY.search(str(exc)):
                raise
            raise LoginError(
                f"{self.host} turned down the login for {self.address} ({exc})"
            ) from None
        status, _ = conn.select(f'"{imap_utf7(self.folder)}"')
        if status != "OK":
            with contextlib.suppress(imaplib.IMAP4.error, OSError):
                conn.logout()
            raise FolderError(f"{self.address} has no folder or label called {self.folder!r}")
        self.conn = conn
        return conn

    def run(self, command: str, *args: str) -> list[object]:
        """One UID command, dropping the connection when it fails so the next poll reconnects."""
        try:
            status, data = self.open().uid(command, *args)
        except (imaplib.IMAP4.abort, OSError):
            self.conn = None
            raise
        if status != "OK":
            raise imaplib.IMAP4.error(f"{command} answered {status}: {data!r}")
        return list(data)

    def unseen(self, terms: list[str]) -> list[str]:
        if not terms:
            return []
        found = self.run("SEARCH", *terms)
        return [uid.decode() for uid in found[0].split()] if found and found[0] else []  # type: ignore[attr-defined]

    def message(self, uid: str) -> Message:
        # PEEK leaves it unread, so mail it decides not to answer stays as it was.
        data = self.run("FETCH", uid, "(BODY.PEEK[])")
        raw = next(part[1] for part in data if isinstance(part, tuple))
        return email.message_from_bytes(raw, policy=email_policy)

    def mark_read(self, uid: str) -> None:
        self.run("STORE", uid, "+FLAGS", "(\\Seen)")

    def close(self) -> None:
        if self.conn:
            with contextlib.suppress(imaplib.IMAP4.error, OSError):
                self.conn.logout()
            self.conn = None


def smtp_login(host: str, address: str, password: str) -> smtplib.SMTP:
    """A logged-in SMTP connection, with TLS from the start on 465 and STARTTLS otherwise."""
    server, port = split_host(host, 465)
    context = tls_for(server)
    if port == 465:
        smtp: smtplib.SMTP = smtplib.SMTP_SSL(
            server, port, timeout=REQUEST_TIMEOUT, context=context
        )
    else:
        smtp = smtplib.SMTP(server, port, timeout=REQUEST_TIMEOUT)
    try:
        if port != 465:
            smtp.starttls(context=context)
        smtp.login(address, password)
    except BaseException:
        smtp.close()
        raise
    return smtp


def send_mail(host: str, address: str, password: str, message: EmailMessage) -> bool:
    try:
        with smtp_login(host, address, password) as smtp:
            smtp.send_message(message)
    except (smtplib.SMTPException, OSError) as exc:
        logger.error("Couldn't email %s: %s", message["To"], exc)
        return False
    return True


class MailInbox:
    """Unread mail from allowed senders in one folder, each marked read before it's answered."""

    def __init__(
        self,
        box: Mailbox,
        smtp_host: str,
        *,
        allowed: set[str],
        sms_senders: set[str],
        text_phone: Callable[[str], bool] | None,
        phone: str = "",
        dry_run: bool = False,
    ) -> None:
        self.box = box
        self.smtp_host = smtp_host
        self.allowed = allowed
        self.sms_senders = sms_senders
        self.text_phone = text_phone
        self.phone = phone
        self.dry_run = dry_run
        self.terms = search_terms(allowed)
        # UIDs it won't look at again, as they're left unread: mail waiting at start-up,
        # mail that only looked like it came from an allowed sender, and dry runs.
        self.skip: set[str] = set()
        self.failures: dict[str, int] = {}

    def skip_waiting(self) -> int:
        waiting = self.box.unseen(self.terms)
        self.skip.update(waiting)
        return len(waiting)

    def answers_by_sms(self, sender: str) -> bool:
        """Carrier gateways and REPLY_BY_SMS get a short answer, and other mail gets email."""
        if sender_permitted(sender, self.sms_senders):
            return True
        return bool(PHONE_GATEWAY.fullmatch(sender.partition("@")[0]))

    def email(self, message: Message, text: str) -> bool:
        reply = reply_to(message, text, self.box.address)
        if self.dry_run:
            logger.info("Dry run, so not emailing %s: %s", reply["To"], text)
            return True
        return send_mail(self.smtp_host, self.box.address, self.box.password, reply)

    def fetch(self) -> Iterator[Incoming]:
        for uid in self.box.unseen(self.terms):
            if uid in self.skip:
                continue
            try:
                message = self.box.message(uid)
            except (imaplib.IMAP4.abort, OSError):
                raise  # the connection is gone, so the rest of this poll would fail too
            except Exception as exc:  # one unreadable message shouldn't hold up the rest
                self._failed(uid, "read", exc)
                continue
            sender = sender_address(message)
            if not sender_permitted(sender, self.allowed):
                self.skip.add(uid)
                logger.warning("Ignoring mail from %r as it isn't in ALLOWED_SENDERS", sender)
                continue
            # Marked read before the reply goes out. The other way round, a message
            # that can't be marked gets answered (and billed) again on every poll.
            if self.dry_run:
                self.skip.add(uid)
            else:
                try:
                    self.box.mark_read(uid)
                except (imaplib.IMAP4.abort, OSError):
                    raise
                except imaplib.IMAP4.error as exc:
                    self._failed(uid, "mark as read", exc)
                    continue
            incoming = self._incoming(uid, message, sender)
            if incoming:
                yield incoming

    def _failed(self, uid: str, doing: str, exc: Exception) -> None:
        self.failures[uid] = self.failures.get(uid, 0) + 1
        if self.failures[uid] < MOST_TRIES:
            logger.error("Couldn't %s message %s (%s), so trying again next poll", doing, uid, exc)
            return
        self.skip.add(uid)
        logger.error(
            "Couldn't %s message %s after %d tries (%s), so it's left alone until a restart",
            doing,
            uid,
            MOST_TRIES,
            exc,
        )

    def _incoming(self, uid: str, message: Message, sender: str) -> Incoming | None:
        query = extract_query(message)
        if not query:
            logger.warning("Message %s had no text to answer", uid)
            return None
        if self.answers_by_sms(sender):
            if self.text_phone:
                return Incoming(sender, query, "sms", self.text_phone, self.phone)
            # With no Twilio, the gateway turns an email back into a text to that phone.
            return Incoming(sender, query, "sms", lambda text: self.email(message, text))
        if NO_REPLY.search(sender.partition("@")[0]):
            # Garmin inReach mail comes from no.reply.inreach@garmin.com, and only the
            # link inside it reaches the device.
            logger.warning(
                "Not answering %s, as it's a no-reply address and an email back would go "
                "nowhere. Text the Twilio number from that device instead.",
                sender,
            )
            return None
        return Incoming(sender, query, "email", lambda text: self.email(message, text))
