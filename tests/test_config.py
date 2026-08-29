import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from remotesearch.config import (
    KEYS,
    beside_config,
    load_config,
    phone_number,
    save_config,
    sender_list,
    sender_permitted,
    setting,
    word_list,
)

EXAMPLE = Path(__file__).parent.parent / "config.example.txt"


def test_setting_rejects_bad_numbers() -> None:
    assert setting({}, "POLL_INTERVAL", 5) == 5
    assert setting({"POLL_INTERVAL": ""}, "POLL_INTERVAL", 5) == 5
    assert setting({"POLL_INTERVAL": "12"}, "POLL_INTERVAL", 5) == 12
    for bad in ("0", "-3", "ten", "1.5"):
        with pytest.raises(SystemExit, match=repr(bad).replace(".", r"\.")):
            setting({"POLL_INTERVAL": bad}, "POLL_INTERVAL", 5)


def test_load_config_lets_the_environment_win() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "config.txt"
        # Notepad can save with a byte order mark, which mustn't end up in the first key.
        path.write_text(
            "MAIL_FOLDER=Texts\n# PHONE_TO=+1\nPOLL_INTERVAL = 9\nAI_MODEL=m\n",
            encoding="utf-8-sig",
        )
        with patch.dict(os.environ, {"POLL_INTERVAL": "30", "BRAVE_API_KEY": "b"}, clear=True):
            config = load_config(str(path))
    assert config.pop("_CONFIG_DIR") == str(Path(tmp).resolve())
    assert config == {
        "MAIL_FOLDER": "Texts",
        "POLL_INTERVAL": "30",
        "AI_MODEL": "m",
        "BRAVE_API_KEY": "b",
    }


def test_save_config_keeps_comments_and_order() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "config.txt"
        path.write_text("# who\nALLOWED_SENDERS=+16045551234\n# PHONE_TO=+1\nPOLL_INTERVAL=5\n")
        save_config(path, {"POLL_INTERVAL": "9", "PHONE_TO": "+16045559999"})
        assert path.read_text() == (
            "# who\nALLOWED_SENDERS=+16045551234\n# PHONE_TO=+1\nPOLL_INTERVAL=9\n"
            "PHONE_TO=+16045559999\n"
        )
        save_config(Path(tmp) / "new.txt", {"PHONE_TO": "+1"})
        assert (Path(tmp) / "new.txt").read_text() == "PHONE_TO=+1\n"


def test_the_example_config_has_every_setting() -> None:
    example = EXAMPLE.read_text(encoding="utf-8")
    for key in KEYS:
        assert f"\n{key}=" in example, key


def test_word_list() -> None:
    assert word_list({"COMMANDS_OFF": " Avy, road,,TIDE "}, "COMMANDS_OFF") == {
        "avy",
        "road",
        "tide",
    }
    assert word_list({}, "COMMANDS_OFF") == set()


def test_allowed_senders_reads_every_kind() -> None:
    assert sender_list({}) == set()
    raw = " Me@Example.com , @txt.bell.ca ,, +1 (604) 555-1234, inreach.garmin.com"
    parsed = sender_list({"ALLOWED_SENDERS": raw})
    assert parsed == {"me@example.com", "txt.bell.ca", "+16045551234", "inreach.garmin.com"}


@pytest.mark.parametrize("bad", ["604-555-1234", "+1604", "+abc", "localhost", "bob@local"])
def test_sender_list_refuses_what_it_cant_read(bad: str) -> None:
    with pytest.raises(SystemExit, match=bad.replace("+", r"\+")):
        sender_list({"REPLY_BY_SMS": bad}, "REPLY_BY_SMS")


def test_phone_number() -> None:
    assert phone_number("+1 604-555-1234") == "+16045551234"
    assert phone_number("+44 20 7946 0958") == "+442079460958"
    assert phone_number("6045551234") is None  # no country code, so it's ambiguous
    assert phone_number("") is None


def test_sender_permitted_emails() -> None:
    allowed = {"me@example.com", "txt.bell.ca"}
    assert sender_permitted("me@example.com", allowed) is True
    assert sender_permitted("5551234@txt.bell.ca", allowed) is True  # domain match
    assert sender_permitted("5551234@evil.txt.bell.ca", allowed) is False
    assert sender_permitted("stranger@spam.net", allowed) is False
    assert sender_permitted("", allowed) is False
    assert sender_permitted("anyone@anywhere.org", set()) is False


def test_sender_permitted_phones_always_need_listing() -> None:
    assert sender_permitted("+16045551234", {"+16045551234"}) is True
    assert sender_permitted("+16045559999", {"+16045551234", "txt.bell.ca"}) is False
    assert sender_permitted("+16045551234", set()) is False


def test_a_save_changes_the_copy_that_gets_read(tmp_path: Path) -> None:
    path = tmp_path / "config.txt"
    path.write_text("PHONE_TO=+1\nPHONE_TO=+2\n", encoding="utf-8")
    save_config(path, {"PHONE_TO": "+3"})
    assert path.read_text(encoding="utf-8") == "PHONE_TO=+1\nPHONE_TO=+3\n"
    assert load_config(path)["PHONE_TO"] == "+3"


def test_relative_paths_are_beside_the_config(tmp_path: Path) -> None:
    path = tmp_path / "trip" / "config.txt"
    path.parent.mkdir()
    path.write_text("PLUGINS_DIR=mine\n", encoding="utf-8")
    config = load_config(path)
    assert beside_config(config, "PLUGINS_DIR", "plugins") == path.parent.resolve() / "mine"
    assert beside_config(config, "STATE_FILE", str(tmp_path / "s.json")) == tmp_path / "s.json"
