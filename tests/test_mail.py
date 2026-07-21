import email
import imaplib
import re
import ssl
from email.message import EmailMessage
from email.policy import default as email_policy
from typing import Any, ClassVar
from unittest.mock import patch

import pytest

from remotesearch.mail import (
    FolderError,
    LoginError,
    Mailbox,
    MailInbox,
    extract_query,
    imap_utf7,
    reply_to,
    search_terms,
    sender_address,
)

ALLOWED = {"txt.bell.ca", "me@example.com"}


def raw(sender: str, text: str = "help", subject: str = "", html: str = "") -> bytes:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = "search@gmail.com"
    message["Message-ID"] = "<q1@mail>"
    if subject:
        message["Subject"] = subject
    if text:
        message.set_content(text)
    if html:
        if text:
            message.add_alternative(html, subtype="html")
        else:
            message.set_content(html, subtype="html")
    return message.as_bytes()


def parsed(data: bytes) -> Any:
    return email.message_from_bytes(data, policy=email_policy)


def verifies(context: ssl.SSLContext) -> bool:
    """Whether a TLS context checks the certificate and the host name it's for."""
    return context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname


class FakeIMAP:
    """Enough of an IMAP server for the inbox: UID SEARCH by FROM, FETCH and STORE."""

    password = "app-password"
    folders = ("INBOX", "Remote Server")

    def __init__(self, messages: dict[str, bytes] | None = None) -> None:
        self.messages = dict(messages or {})
        self.seen: set[str] = set()
        self.events: list[str] = []
        self.store_error = False

    def __call__(
        self, host: str, port: int, ssl_context: ssl.SSLContext, timeout: float
    ) -> "FakeIMAP":
        assert verifies(ssl_context)
        self.events.append(f"connect {host}:{port}")
        return self

    def login(self, user: str, password: str) -> tuple[str, list[bytes]]:
        if password != self.password:
            raise imaplib.IMAP4.error("[AUTHENTICATIONFAILED] Invalid credentials")
        return "OK", [b"logged in"]

    def select(self, folder: str) -> tuple[str, list[bytes]]:
        return ("OK", [b"1"]) if folder.strip('"') in self.folders else ("NO", [b"no folder"])

    def shutdown(self) -> None:
        self.events.append("shutdown")

    def logout(self) -> None:
        self.events.append("logout")

    def uid(self, command: str, *args: str) -> tuple[str, list[Any]]:
        self.events.append(command)
        if command == "SEARCH":
            wanted = re.findall(r'FROM "([^"]+)"', " ".join(args))
            found = [
                uid
                for uid, data in self.messages.items()
                if uid not in self.seen and any(w in str(parsed(data)["From"]) for w in wanted)
            ]
            return "OK", [" ".join(found).encode()]
        if command == "FETCH":
            assert args[1] == "(BODY.PEEK[])"
            return "OK", [(b"1 (UID 1 BODY[] {9}", self.messages[args[0]]), b")"]
        if command == "STORE":
            if self.store_error:
                return "NO", [b"read only"]
            self.seen.add(args[0])
            return "OK", []
        raise AssertionError(command)


def inbox(fake: FakeIMAP, *, text_phone: Any = None, dry_run: bool = False, **kw: Any) -> MailInbox:
    box = Mailbox("search@gmail.com", "app-password", "imap.gmail.com")
    return MailInbox(
        box,
        "smtp.gmail.com:465",
        allowed=kw.get("allowed", ALLOWED),
        sms_senders=kw.get("sms_senders", set()),
        text_phone=text_phone,
        phone=kw.get("phone", ""),
        dry_run=dry_run,
    )


@pytest.fixture
def imap() -> Any:
    fake = FakeIMAP()
    with patch("imaplib.IMAP4_SSL", fake):
        yield fake


class FakeSMTP:
    sent: ClassVar[list[EmailMessage]] = []
    used: ClassVar[list[str]] = []

    def __init__(
        self, host: str, port: int, timeout: float, context: ssl.SSLContext | None = None
    ) -> None:
        assert context is None or verifies(context)
        FakeSMTP.used.append(f"{type(self).__name__} {host}:{port}")

    def __enter__(self) -> "FakeSMTP":
        return self

    def __exit__(self, *exc: object) -> None:
        pass

    def starttls(self, context: ssl.SSLContext) -> None:
        assert verifies(context)
        FakeSMTP.used.append("starttls")

    def login(self, user: str, password: str) -> None:
        pass

    def send_message(self, message: EmailMessage) -> None:
        FakeSMTP.sent.append(message)


@pytest.fixture
def smtp() -> Any:
    FakeSMTP.sent, FakeSMTP.used = [], []
    with patch("smtplib.SMTP_SSL", FakeSMTP), patch("smtplib.SMTP", FakeSMTP):
        yield FakeSMTP


def test_the_server_filters_by_sender() -> None:
    assert search_terms({"+16045551234"}) == []
    assert search_terms({"txt.bell.ca"}) == ["UNSEEN", 'FROM "txt.bell.ca"']
    assert search_terms(ALLOWED | {"+16045551234"}) == [
        "UNSEEN",
        'OR FROM "me@example.com" FROM "txt.bell.ca"',
    ]
    three = search_terms({"a.ca", "b.ca", "c.ca"})
    assert three[1] == 'OR OR FROM "a.ca" FROM "b.ca" FROM "c.ca"'


def test_extract_query_prefers_plain_text() -> None:
    both = parsed(raw("a@b.co", "define albedo\nSent from my iPhone", html="<p>ignored</p>"))
    assert extract_query(both) == "define albedo"
    only_html = parsed(raw("a@b.co", "", html="<div>tide <b>Tofino</b></div>"))
    assert extract_query(only_html) == "tide Tofino"
    assert extract_query(parsed(raw("a@b.co", ""))) is None


def test_sender_address_ignores_the_display_name() -> None:
    # A display name can hold an allowed address, and only the one in brackets counts.
    fake = parsed(raw('"me@example.com" <stranger@spam.net>'))
    assert sender_address(fake) == "stranger@spam.net"
    assert sender_address(parsed(raw("Me <Me@Example.com>"))) == "me@example.com"
    assert sender_address(parsed(raw("nobody"))) == ""


def test_reply_threads_under_the_question() -> None:
    reply = reply_to(parsed(raw("me@example.com", subject="tide")), "high 3pm", "s@gmail.com")
    assert reply["To"] == "me@example.com"
    assert reply["From"] == "s@gmail.com"
    assert reply["Subject"] == "Re: tide"
    assert reply["In-Reply-To"] == "<q1@mail>"
    already = reply_to(parsed(raw("me@example.com", subject="RE: tide")), "x", "s@gmail.com")
    assert already["Subject"] == "RE: tide"
    assert reply_to(parsed(raw("me@example.com")), "x", "s")["Subject"] == "Re: your question"


def test_gateway_mail_is_texted_to_phone_to(imap: FakeIMAP) -> None:
    imap.messages = {"7": raw("6045551234@txt.bell.ca", "define albedo")}
    texted: list[str] = []

    def text_phone(text: str) -> bool:
        texted.append(text)
        return True

    found = list(inbox(imap, text_phone=text_phone).fetch())
    assert [(i.sender, i.text, i.channel) for i in found] == [
        ("6045551234@txt.bell.ca", "define albedo", "sms")
    ]
    assert found[0].send("answer") is True
    assert texted == ["answer"]
    assert imap.seen == {"7"}


def test_without_twilio_the_gateway_gets_an_email_back(imap: FakeIMAP, smtp: Any) -> None:
    imap.messages = {"7": raw("6045551234@txt.bell.ca", "define albedo")}
    (found,) = inbox(imap).fetch()
    assert found.channel == "sms"  # still paged to SMS size, as the carrier turns it into one
    assert found.send("define: reflectivity") is True
    assert smtp.sent[0]["To"] == "6045551234@txt.bell.ca"
    assert smtp.used == ["FakeSMTP smtp.gmail.com:465"]


def test_plain_email_gets_an_email_reply_over_starttls(imap: FakeIMAP, smtp: Any) -> None:
    imap.messages = {"3": raw("me@example.com", "tide Tofino", subject="tides")}
    box = inbox(imap)
    box.smtp_host = "smtp.example.com:587"
    (found,) = box.fetch()
    assert found.channel == "email"
    assert found.send("high at 3") is True
    assert smtp.sent[0]["Subject"] == "Re: tides"
    assert smtp.used == ["FakeSMTP smtp.example.com:587", "starttls"]


def test_reply_by_sms_overrides_email_for_listed_senders(imap: FakeIMAP) -> None:
    imap.messages = {"3": raw("me@example.com")}
    (found,) = inbox(imap, text_phone=lambda t: True, sms_senders={"me@example.com"}).fetch()
    assert found.channel == "sms"


def test_no_reply_senders_get_nothing(imap: FakeIMAP, caplog: pytest.LogCaptureFixture) -> None:
    imap.messages = {"1": raw("no.reply.inreach@garmin.com")}
    assert list(inbox(imap, allowed={"garmin.com"}).fetch()) == []
    assert "no-reply address" in caplog.text


def test_mail_from_strangers_is_never_fetched_or_marked(imap: FakeIMAP) -> None:
    imap.messages = {"1": raw("stranger@spam.net"), "2": raw("me@example.com")}
    assert [i.sender for i in inbox(imap).fetch()] == ["me@example.com"]
    assert imap.seen == {"2"}
    assert imap.events.count("FETCH") == 1


def test_a_forged_lookalike_is_left_unread_and_skipped(imap: FakeIMAP) -> None:
    # The server's FROM search matches the display name too, so the address is checked again.
    imap.messages = {"1": raw('"me@example.com" <stranger@spam.net>')}
    box = inbox(imap)
    assert list(box.fetch()) == []
    assert list(box.fetch()) == []
    assert imap.seen == set()
    assert imap.events.count("FETCH") == 1


def test_mail_with_no_text_is_marked_and_skipped(imap: FakeIMAP) -> None:
    imap.messages = {"1": raw("me@example.com", "Sent from my iPhone")}
    assert list(inbox(imap).fetch()) == []
    assert imap.seen == {"1"}


def test_nothing_is_handed_on_when_marking_read_fails(imap: FakeIMAP) -> None:
    imap.messages = {"1": raw("me@example.com")}
    imap.store_error = True
    box = inbox(imap)
    assert list(box.fetch()) == []
    assert box.failures == {"1": 1}  # tried again next poll
    list(box.fetch())
    list(box.fetch())
    assert "1" in box.skip  # and left alone after the third try


def test_waiting_mail_is_left_alone_at_start(imap: FakeIMAP) -> None:
    imap.messages = {"1": raw("me@example.com"), "2": raw("me@example.com")}
    box = inbox(imap)
    assert box.skip_waiting() == 2
    imap.messages["3"] = raw("me@example.com", "tide")
    assert [i.text for i in box.fetch()] == ["tide"]
    assert imap.seen == {"3"}


def test_dry_runs_send_nothing_and_mark_nothing(imap: FakeIMAP, smtp: Any) -> None:
    imap.messages = {"1": raw("me@example.com")}
    box = inbox(imap, dry_run=True)
    (found,) = box.fetch()
    assert found.send("answer") is True
    assert list(box.fetch()) == []
    assert imap.seen == set()
    assert smtp.sent == []


def test_a_wrong_password_is_refused(imap: FakeIMAP) -> None:
    imap.password = "something else"
    with pytest.raises(LoginError, match=r"turned down the login for search@gmail\.com"):
        list(inbox(imap).fetch())


def test_a_missing_folder_says_so(imap: FakeIMAP) -> None:
    box = Mailbox("search@gmail.com", "app-password", "imap.gmail.com", "Texts")
    with pytest.raises(FolderError, match="no folder or label called 'Texts'"):
        box.unseen(["UNSEEN"])


def test_a_busy_server_isnt_taken_for_a_wrong_password(imap: FakeIMAP) -> None:
    busy = imaplib.IMAP4.error("[UNAVAILABLE] Temporary System Error")
    with (
        patch.object(imap, "login", side_effect=busy),
        pytest.raises(imaplib.IMAP4.error) as raised,
    ):
        list(inbox(imap).fetch())
    assert not isinstance(raised.value, LoginError)


def test_folder_names_go_out_in_imap_utf7() -> None:
    assert imap_utf7("Réponses") == "R&AOk-ponses"
    assert imap_utf7("Q&A") == "Q&-A"
    assert imap_utf7("INBOX") == "INBOX"


def test_a_non_ascii_sender_stops_at_start() -> None:
    with pytest.raises(SystemExit, match="isn't plain ASCII"):
        search_terms({"josé@exemple.ca"})


def test_a_broken_from_line_has_no_sender() -> None:
    assert sender_address(parsed(b"From: (x):me@example.com\r\n\r\nhelp")) == ""


def test_a_message_that_wont_fetch_is_tried_again(imap: FakeIMAP) -> None:
    imap.messages = {"1": raw("me@example.com", "tide")}
    box = inbox(imap)
    real = imap.uid
    with patch.object(
        imap, "uid", side_effect=lambda c, *a: ("NO", [b"busy"]) if c == "FETCH" else real(c, *a)
    ):
        assert list(box.fetch()) == []
    assert [i.text for i in box.fetch()] == ["tide"]


def test_a_dropped_connection_reconnects_next_poll(imap: FakeIMAP) -> None:
    box = inbox(imap)
    list(box.fetch())
    with (
        patch.object(imap, "uid", side_effect=imaplib.IMAP4.abort("socket closed")),
        pytest.raises(imaplib.IMAP4.abort),
    ):
        list(box.fetch())
    assert box.box.conn is None
    list(box.fetch())
    assert imap.events.count("connect imap.gmail.com:993") == 2


def test_a_reply_loses_the_thread_and_signature_under_it() -> None:
    body = (
        "tide Tofino\n\n-- \nSent from the trail\n\n"
        "On Mon, Nov 3, 2026 at 9:12 AM Remote Search <search@gmail.com>\nwrote:\n> help"
    )
    assert extract_query(parsed(raw("me@example.com", body))) == "tide Tofino"
    quoted = "weather Whistler\n\nOn Mon, Nov 3, 2026, Me wrote:\n> tide Tofino\n"
    assert extract_query(parsed(raw("me@example.com", quoted))) == "weather Whistler"


def test_an_html_reply_loses_the_quote_too() -> None:
    html = (
        "<div>avy Rogers Pass</div><div class=gmail_quote>"
        "<div>On Mon, Nov 3, 2026 Me wrote:</div><blockquote>tide Tofino</blockquote></div>"
    )
    assert extract_query(parsed(raw("me@example.com", "", html=html))) == "avy Rogers Pass"


def test_a_gateway_answer_by_text_takes_its_more_from_the_phone(imap: FakeIMAP) -> None:
    imap.messages = {"7": raw("6045551234@txt.bell.ca", "define albedo")}
    found = list(inbox(imap, text_phone=lambda _t: True, phone="+16045551234").fetch())
    assert found[0].replies_to == "+16045551234"
