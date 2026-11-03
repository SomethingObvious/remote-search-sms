import json
import tempfile
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import requests

from remotesearch.android import Phone, PhoneError, PhoneInbox, make_phone_texter
from remotesearch.checks import android, send_test_text
from remotesearch.poller import Poller
from remotesearch.reply import Answerer
from remotesearch.state import State

HELP_TEXT = Answerer().help_text()
PHONE = {
    "ANDROID_URL": "192.168.1.50:8080",
    "ANDROID_USER": "sms",
    "ANDROID_PASSWORD": "secret",
}


def received(n: int, text: str, sender: str = "+16045551234", minutes_ago: int = 1) -> dict:
    at = datetime.now(UTC) - timedelta(minutes=minutes_ago)
    return {
        "id": f"text:{n}",
        "type": "SMS",
        "sender": sender,
        "recipient": "+17785550000",
        "simNumber": 1,
        "contentPreview": text,
        "createdAt": at.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
    }


class Reply:
    def __init__(self, status: int, data: Any = None) -> None:
        self.status_code = status
        self.ok = status < 400
        self.text = json.dumps(data)
        self.content = self.text.encode() if data is not None else b""

    def json(self) -> Any:
        return json.loads(self.text)


class FakePhone:
    """SMS Gateway for Android's local server: /inbox to list texts, /messages to send."""

    def __init__(self) -> None:
        self.inbox: list[dict] = []
        self.sent: list[dict] = []
        self.calls: list[str] = []
        self.status = 0  # answer every call with this when it's set

    def __call__(self, method: str, url: str, *, auth: Any, timeout: float, **kw: Any) -> Reply:
        assert url.startswith("http://192.168.1.50:8080/")
        path = url.removeprefix("http://192.168.1.50:8080")
        self.calls.append(f"{method} {path}")
        if self.status:
            return Reply(self.status, {"message": "nope"})
        if auth != ("sms", "secret"):
            return Reply(401, {"message": "Unauthorized"})
        if path == "/inbox":
            since = datetime.fromisoformat(kw["params"]["from"])
            listed = [m for m in self.inbox if datetime.fromisoformat(m["createdAt"]) >= since]
            listed.sort(key=lambda m: m["createdAt"], reverse=True)  # the app lists newest first
            return Reply(200, listed[: kw["params"]["limit"]])
        if path == "/messages":
            self.sent.append(kw["json"])
            return Reply(202, {"id": "m1", "state": "Pending"})
        return Reply(404, {"message": "Not Found"})


@pytest.fixture
def fake() -> Iterator[FakePhone]:
    phone = FakePhone()
    with patch("requests.request", phone):
        yield phone


@pytest.fixture
def folder() -> Iterator[Path]:
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


def phone_inbox(folder: Path, *, catch_up: bool = False) -> PhoneInbox:
    phone = Phone(PHONE["ANDROID_URL"], PHONE["ANDROID_USER"], PHONE["ANDROID_PASSWORD"])
    return PhoneInbox(
        phone,
        State(folder / "s.json"),
        make_phone_texter(phone),
        allowed={"+16045551234"},
        catch_up=catch_up,
    )


def answer_all(inbox: PhoneInbox) -> list[str]:
    return [m.text for m in inbox.fetch() if m.send(f"re {m.text}")]


def test_texts_waiting_at_the_first_start_are_set_aside(fake: FakePhone, folder: Path) -> None:
    fake.inbox = [received(1, "tide Tofino", minutes_ago=30)]
    assert answer_all(phone_inbox(folder)) == []
    fake.inbox.append(received(2, "weather Tofino"))
    assert answer_all(phone_inbox(folder)) == ["weather Tofino"]
    assert fake.sent == [
        {"textMessage": {"text": "re weather Tofino"}, "phoneNumbers": ["+16045551234"]}
    ]


def test_each_text_is_answered_once_oldest_first(fake: FakePhone, folder: Path) -> None:
    fake.inbox = [received(1, "first", minutes_ago=3), received(2, "second", minutes_ago=2)]
    assert answer_all(phone_inbox(folder, catch_up=True)) == ["first", "second"]
    assert answer_all(phone_inbox(folder)) == []
    assert len(fake.sent) == 2


def test_only_allowed_senders_get_an_answer(fake: FakePhone, folder: Path) -> None:
    fake.inbox = [
        received(1, "help", sender="+15550001111"),
        received(2, "help", sender="SHORTCODE"),
    ]
    assert answer_all(phone_inbox(folder, catch_up=True)) == []
    assert fake.sent == []


def test_the_same_words_twice_are_two_texts(fake: FakePhone, folder: Path) -> None:
    # The app's id is a hash of the text, so it can repeat.
    fake.inbox = [received(7, "more", minutes_ago=2), received(7, "more", minutes_ago=1)]
    assert answer_all(phone_inbox(folder, catch_up=True)) == ["more", "more"]


@pytest.mark.parametrize(
    ("status", "said"),
    [
        (401, "turned down the username and password"),
        (404, "Update it to 1.59 or newer"),
        (500, "answered 500 to /inbox"),
    ],
)
def test_the_phone_says_what_went_wrong(fake: FakePhone, status: int, said: str) -> None:
    fake.status = status
    with pytest.raises(PhoneError, match=said):
        Phone("http://192.168.1.50:8080/", "sms", "secret").inbox(datetime.now(UTC))


def test_a_phone_off_the_network_is_a_failed_send_not_a_crash() -> None:
    with patch("requests.request", side_effect=requests.ConnectionError("refused")):
        text = make_phone_texter(Phone("192.168.1.50:8080", "sms", "secret"))
        assert text("+16045551234", "hi") is False


def test_the_check_reaches_the_phone(fake: FakePhone) -> None:
    assert android(PHONE).ok is True
    assert android({"ANDROID_URL": "192.168.1.50:8080"}).detail == (
        "ANDROID_USER and ANDROID_PASSWORD still need filling in."
    )
    assert android({}).ok is None
    assert android(PHONE | {"ANDROID_PASSWORD": "wrong"}).detail.endswith(
        "turned down the username and password."
    )


def test_a_test_text_goes_through_the_phone_when_both_are_set_up(fake: FakePhone) -> None:
    twilio = {"TWILIO_ACCOUNT_SID": "AC1", "TWILIO_AUTH_TOKEN": "t", "TWILIO_PHONE_FROM": "+1778"}
    config = PHONE | twilio | {"PHONE_TO": "+16045551234"}
    assert send_test_text(config) == "+16045551234"
    assert fake.sent[0]["phoneNumbers"] == ["+16045551234"]
    with pytest.raises(ValueError, match="Android phone isn't set up"):
        send_test_text(twilio | {"PHONE_TO": "+16045551234"}, "Android phone")


def test_the_poller_answers_texts_to_the_phone(fake: FakePhone, folder: Path) -> None:
    config = {"ALLOWED_SENDERS": "+16045551234", "STATE_FILE": str(folder / "s.json")} | PHONE
    built = Poller(config, catch_up=True)
    try:
        fake.inbox = [received(1, "help")]
        assert built.poll() == 1
        assert fake.sent[0]["textMessage"]["text"] == HELP_TEXT
    finally:
        built.close()


def test_a_phone_number_with_no_way_to_text_stops_at_start(folder: Path) -> None:
    config = {"ALLOWED_SENDERS": "+16045551234", "STATE_FILE": str(folder / "s.json")}
    with pytest.raises(SystemExit, match="Fill in the Twilio settings or the Android phone's"):
        Poller(config)
