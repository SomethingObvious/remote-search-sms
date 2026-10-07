import json
import logging
import tempfile
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from test_sms import FakeMessages

from remotesearch.mail import LoginError
from remotesearch.poller import HourlyCap, Poller, pause
from remotesearch.reply import Answerer, Incoming
from remotesearch.state import State

HELP_TEXT = Answerer().help_text()
TWILIO = {
    "TWILIO_ACCOUNT_SID": "ACtest",
    "TWILIO_AUTH_TOKEN": "token",
    "TWILIO_PHONE_FROM": "+17785550000",
}


OPEN: list[Poller] = []


@pytest.fixture
def folder() -> Iterator[Path]:
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)
        # Each holds a lock on its state file, which Windows won't delete.
        while OPEN:
            OPEN.pop().close()


def poller(folder: Path, messages: FakeMessages | None = None, /, **settings: str) -> Poller:
    config = {"ALLOWED_SENDERS": "+16045551234", "STATE_FILE": str(folder / "s.json")}
    client = SimpleNamespace(messages=messages or FakeMessages())
    with patch("remotesearch.poller.twilio_client", return_value=client):
        built = Poller(config | TWILIO | settings, catch_up=True)
    OPEN.append(built)
    return built


class ListInbox:
    def __init__(self, items: list[Incoming]) -> None:
        self.items = items
        self.handed = 0

    def fetch(self) -> Iterator[Incoming]:
        for item in self.items:
            self.handed += 1
            yield item


def items(sent: list[str], count: int, sender: str = "+16045551234") -> list[Incoming]:
    def send(text: str) -> bool:
        sent.append(text)
        return True

    return [Incoming(sender, "help", "sms", send) for _ in range(count)]


@pytest.mark.parametrize(
    ("settings", "message_text"),
    [
        ({"ALLOWED_SENDERS": "me@example.com"}, "doesn't set up anything to watch"),
        ({"ALLOWED_SENDERS": ""}, "ALLOWED_SENDERS is empty"),
        ({"TWILIO_AUTH_TOKEN": ""}, "sets TWILIO_AUTH_TOKEN"),
        ({"MAIL_ADDRESS": "s@gmail.com", "ALLOWED_SENDERS": "txt.bell.ca"}, "sets MAIL_PASSWORD"),
        ({"MAX_REPLIES_PER_HOUR": "0"}, "MAX_REPLIES_PER_HOUR has to be a whole number"),
        ({"MAX_SMS_CHARS": "2000"}, "won't send more than 1600"),
        ({"ALLOWED_SENDERS": "555-1234"}, "isn't an email address"),
        ({"NEWS_REGION": "Canada"}, "NEWS_REGION is 'Canada'"),
        ({"AI_API_KEY": "nope"}, "doesn't match a provider"),
    ],
)
def test_a_config_it_cant_run_stops_at_start(
    folder: Path, settings: dict[str, str], message_text: str
) -> None:
    with pytest.raises(SystemExit, match=message_text):
        poller(folder, **settings)


def test_the_caps_come_from_the_config(folder: Path) -> None:
    built = poller(folder, POLL_INTERVAL="9", MAX_REPLIES_PER_POLL="4", MAX_REPLIES_PER_HOUR="7")
    assert (built.interval, built.max_replies) == (9, 4)
    assert built.hourly is not None
    assert built.hourly.most == 7


def test_a_poll_caps_replies_across_every_inbox(folder: Path) -> None:
    # Every reply is a billed SMS, so a flood of mail can't turn into a flood of texts.
    sent: list[str] = []
    built = poller(folder, MAX_REPLIES_PER_POLL="3")
    first, second = ListInbox(items(sent, 2)), ListInbox(items(sent, 5))
    built.inboxes = [first, second]
    assert built.poll() == 3
    assert sent == [HELP_TEXT] * 3
    assert (first.handed, second.handed) == (2, 1)  # the rest stay waiting for the next poll


def test_one_bad_question_doesnt_stop_the_rest(folder: Path) -> None:
    sent: list[str] = []
    built = poller(folder)
    failing = Incoming("+1", "help", "sms", lambda _t: False)
    built.inboxes = [ListInbox([items(sent, 1)[0], failing, *items(sent, 1)])]
    with patch.object(built.responder, "reply", side_effect=[RuntimeError("bug"), "ok", "ok"]):
        assert built.poll() == 1
    assert sent == ["ok"]
    assert [r["reply"] for r in built.recent] == ["ok", "ok"]
    assert [r["sent"] for r in built.recent] == [True, False]


def test_failing_polls_back_off_until_a_login_is_refused(folder: Path) -> None:
    class Broken:
        def __init__(self) -> None:
            self.errors = [OSError("down"), OSError("still down"), LoginError("refused")]

        def fetch(self) -> Iterator[Incoming]:
            raise self.errors.pop(0)

    built = poller(folder)
    built.inboxes = [Broken()]
    stop = threading.Event()
    with patch("remotesearch.poller.pause") as wait, pytest.raises(LoginError):
        built.run(stop)
    assert [c.args[1] for c in wait.call_args_list] == [10, 20]


def test_run_stops_when_asked(folder: Path) -> None:
    built = poller(folder)
    built.inboxes = [ListInbox([])]
    stop = threading.Event()
    with patch("remotesearch.poller.pause", side_effect=lambda *_: stop.set()):
        built.run(stop)
    assert stop.is_set()


def test_the_hourly_cap_spans_every_inbox_and_restarts(folder: Path) -> None:
    sent: list[str] = []
    built = poller(folder, MAX_REPLIES_PER_HOUR="3")
    mail, texts = ListInbox(items(sent, 2, "me@example.com")), ListInbox(items(sent, 5))
    built.inboxes = [mail, texts]
    assert built.poll() == 3
    assert (mail.handed, texts.handed) == (2, 1)
    assert len(json.loads((folder / "s.json").read_text(encoding="utf-8"))["sent"]) == 3

    built.close()
    restarted = poller(folder, MAX_REPLIES_PER_HOUR="3")
    later = ListInbox(items(sent, 1))
    restarted.inboxes = [later]
    assert restarted.poll() == 0
    assert later.handed == 0  # still unread or unseen, so it's answered once there's room


def test_a_second_poller_on_one_state_file_is_refused(folder: Path) -> None:
    poller(folder)
    with pytest.raises(SystemExit, match="Another remote-search is already answering"):
        poller(folder)
    config = {"ALLOWED_SENDERS": "+16045551234", "STATE_FILE": str(folder / "s.json")}
    with patch("remotesearch.poller.twilio_client"):
        # A dry run writes nothing, so it can sit beside the real one.
        Poller(config | TWILIO, dry_run=True).close()


def test_a_wait_ends_early_when_stopped() -> None:
    stop = threading.Event()
    stop.set()
    pause(stop, 300)  # returns at once instead of after 5 minutes


@pytest.fixture
def state(folder: Path) -> State:
    return State(folder / "state.json")


def test_replies_older_than_an_hour_dont_count(state: State) -> None:
    old = (datetime.now(UTC) - timedelta(minutes=61)).isoformat()
    state.data["sent"] = [old] * 3
    hourly = HourlyCap(state, 3)
    assert hourly.reached() is False
    hourly.count()
    assert len(state.data["sent"]) == 1
    assert state.data["sent"][0] != old


def test_failed_sends_dont_count_toward_the_hour(folder: Path) -> None:
    built = poller(folder)
    built.inboxes = [ListInbox([Incoming("+1", "help", "sms", lambda _t: False)] * 4)]
    assert built.poll() == 0
    assert built.state.data.get("sent", []) == []


def test_the_hourly_cap_is_logged_once_per_stretch(
    state: State, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, "remotesearch")
    state.data["sent"] = [datetime.now(UTC).isoformat()] * 3
    hourly = HourlyCap(state, 3)
    for _ in range(3):
        hourly.reached()
    assert caplog.text.count("MAX_REPLIES_PER_HOUR") == 1
    state.data["sent"] = []
    hourly.reached()
    state.data["sent"] = [datetime.now(UTC).isoformat()] * 3
    hourly.reached()
    assert caplog.text.count("MAX_REPLIES_PER_HOUR") == 2


def test_texting_the_twilio_number_directly(folder: Path) -> None:
    from test_sms import text_message

    messages = FakeMessages()
    messages.stored = [text_message("SM1", "help")]
    built = poller(folder, messages)
    assert built.poll() == 1
    assert messages.created == [{"to": "+16045551234", "from_": "+17785550000", "body": HELP_TEXT}]
    assert list(json.loads((folder / "s.json").read_text())["twilio"]["seen"]) == ["SM1"]


def test_gateway_mail_announces_the_start_by_text(folder: Path) -> None:
    from test_mail import FakeIMAP

    messages = FakeMessages()
    settings: dict[str, Any] = {
        "ALLOWED_SENDERS": "txt.bell.ca",
        "MAIL_ADDRESS": "search@gmail.com",
        "MAIL_PASSWORD": "app-password",
        "PHONE_TO": "+16045551234",
    }
    with patch("imaplib.IMAP4_SSL", FakeIMAP()):
        built = poller(folder, messages, **settings)
        stop = threading.Event()
        with patch("remotesearch.poller.pause", side_effect=lambda *_: stop.set()):
            built.run(stop)
    assert messages.created[0]["body"].startswith("Remote search is running")


def test_more_after_a_gateway_answer_comes_from_the_phone(folder: Path) -> None:
    sent: list[str] = []

    def send(text: str) -> bool:
        sent.append(text)
        return True

    built = poller(folder, MAX_SMS_CHARS="60")
    built.answer(Incoming("6045551234@txt.bell.ca", "help", "sms", send, "+16045551234"))
    built.answer(Incoming("+16045551234", "more", "sms", send))
    assert " (more 1/" in sent[0]
    assert " (more 2/" in sent[1]
