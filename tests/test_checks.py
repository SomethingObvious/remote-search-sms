from remotesearch.checks import run_all, twilio


def test_a_check_that_stops_early_is_still_shown_under_its_section() -> None:
    config = {"ALLOWED_SENDERS": "me@example.com", "MAIL_ADDRESS": "search@example.com"}
    results = {r.name: r for r in run_all(config)}
    assert list(results) == [
        "Allowed senders",
        "Twilio",
        "Email",
        "AI answers",
        "Brave Search",
        "Commands",
    ]
    assert results["Email"].ok is False
    assert "IMAP_HOST" in results["Email"].detail
    assert results["Twilio"].ok is None


def test_a_half_filled_twilio_section_says_what_is_missing() -> None:
    result = twilio({"TWILIO_PHONE_FROM": "+15551234567"})
    assert result.ok is False
    assert result.detail == "TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN still need filling in."
