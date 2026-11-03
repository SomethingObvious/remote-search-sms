import os
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from conftest import fake_web
from test_sms import FakeMessages, text_message

from remotesearch.checks import TEST_TEXT, Result
from remotesearch.cli import main
from remotesearch.reply import Answerer

DICTIONARY = "https://en.wiktionary.org/"
ALBEDO = {"en": [{"partOfSpeech": "Noun", "definitions": [{"definition": "Reflectivity."}]}]}
TWILIO = {
    "TWILIO_ACCOUNT_SID": "ACtest",
    "TWILIO_AUTH_TOKEN": "token",
    "TWILIO_PHONE_FROM": "+17785550000",
}


@contextmanager
def config_file(lines: dict[str, str]) -> Iterator[Path]:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "config.txt"
        lines = {"STATE_FILE": str(Path(tmp) / "state.json")} | lines
        path.write_text("".join(f"{k}={v}\n" for k, v in lines.items()), encoding="utf-8")
        with patch.dict(os.environ, {}, clear=True):
            yield path


def test_help_runs(capsys: pytest.CaptureFixture) -> None:
    with pytest.raises(SystemExit) as done:
        main(["--help"])
    assert done.value.code == 0
    assert "setup" in capsys.readouterr().out


def test_ask_prints_the_answer_with_no_accounts(capsys: pytest.CaptureFixture) -> None:
    with config_file({}) as path, fake_web({DICTIONARY: ALBEDO}):
        main(["ask", "define", "albedo", "--config", str(path)])
    assert capsys.readouterr().out == "define: (noun) Reflectivity.\n"
    with config_file({}) as path:
        main(["ask", "help", "--config", str(path)])
    assert capsys.readouterr().out == Answerer().help_text() + "\n"


def test_ask_prints_every_page(capsys: pytest.CaptureFixture) -> None:
    with config_file({"MAX_SMS_CHARS": "40"}) as path:
        main(["ask", "help", "--config", str(path)])
    pages = capsys.readouterr().out.strip().split("\n\n")
    assert len(pages) == 6
    assert pages[0].endswith("(more 1/6)")
    assert all(len(p) <= 40 for p in pages)


def test_check_exits_with_1_when_anything_failed(capsys: pytest.CaptureFixture) -> None:
    results = [Result("Twilio", True, "Logged in."), Result("Email", None, "Not set up.")]
    with (
        config_file({}) as path,
        patch("remotesearch.checks.run_all", return_value=results),
        pytest.raises(SystemExit) as done,
    ):
        main(["check", "--config", str(path)])
    assert done.value.code == 0
    assert capsys.readouterr().out == "ok   Twilio: Logged in.\n--   Email: Not set up.\n"
    results.append(Result("AI answers", False, "No answer."))
    with (
        config_file({}) as path,
        patch("remotesearch.checks.run_all", return_value=results),
        pytest.raises(SystemExit) as done,
    ):
        main(["check", "--config", str(path)])
    assert done.value.code == 1


def test_check_can_send_a_test_text(capsys: pytest.CaptureFixture) -> None:
    messages = FakeMessages()
    client = SimpleNamespace(messages=messages)
    with (
        config_file({**TWILIO, "PHONE_TO": "+16045551234"}) as path,
        patch("remotesearch.checks.run_all", return_value=[]),
        patch("remotesearch.sms.twilio_client", return_value=client),
        pytest.raises(SystemExit) as done,
    ):
        main(["check", "--text", "--config", str(path)])
    assert done.value.code == 0
    assert messages.created == [{"to": "+16045551234", "from_": "+17785550000", "body": TEST_TEXT}]


def test_run_dry_once_answers_without_sending_or_saving() -> None:
    messages = FakeMessages()
    messages.stored = [text_message("SM1", "define albedo")]
    client = SimpleNamespace(messages=messages)
    with (
        config_file({**TWILIO, "ALLOWED_SENDERS": "+16045551234"}) as path,
        patch("remotesearch.poller.twilio_client", return_value=client),
        patch("remotesearch.sms.logger") as log,
        fake_web({DICTIONARY: ALBEDO}),
    ):
        main(["run", "--once", "--dry-run", "--catch-up", "--config", str(path)])
        saved = path.with_name("state.json").exists()
    log.info.assert_called_with(
        "Dry run, so not texting %s: %s", "+16045551234", "define: (noun) Reflectivity."
    )
    assert messages.created == []
    assert saved is False


def test_a_refused_mail_login_ends_with_advice() -> None:
    from test_mail import FakeIMAP

    fake = FakeIMAP()
    fake.password = "the real one"
    settings = {
        "ALLOWED_SENDERS": "txt.bell.ca",
        "MAIL_ADDRESS": "search@gmail.com",
        "MAIL_PASSWORD": "app-password",
    }
    with (
        config_file(settings) as path,
        patch("imaplib.IMAP4_SSL", fake),
        pytest.raises(SystemExit, match="has to be an app password"),
    ):
        main(["run", "--config", str(path)])


def test_config_can_come_before_the_action() -> None:
    from remotesearch.cli import parse_args

    args = parse_args(["--config", "D:/trip/config.txt", "--verbose", "run", "--once"])
    assert (args.config, args.verbose) == ("D:/trip/config.txt", True)
    args = parse_args(["run", "--config", "D:/trip/config.txt"])
    assert (args.config, args.verbose) == ("D:/trip/config.txt", False)


def test_no_action_is_an_error(capsys: pytest.CaptureFixture) -> None:
    with pytest.raises(SystemExit) as done:
        main([])
    assert done.value.code == 2
    assert "required: {setup,ask,check,run}" in capsys.readouterr().err


def test_a_run_with_no_terminal_says_why_it_stopped(tmp_path: Path) -> None:
    (tmp_path / "config.txt").write_text("ALLOWED_SENDERS=\n", encoding="utf-8")
    log = tmp_path / "remote-search.log"
    done = subprocess.run(
        [sys.executable, "-m", "remotesearch", "run", "--config", "config.txt", "--log", str(log)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 1
    assert "ERROR Stopped: ALLOWED_SENDERS is empty" in log.read_text(encoding="utf-8")
