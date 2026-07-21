import pytest
from conftest import fake_web, fixture_text

from remotesearch.commands.news import edition, headlines
from remotesearch.net import SourceError

NEWS = "https://news.google.com/rss"
CANADA = {"hl": "en-CA", "gl": "CA", "ceid": "CA:en"}


def test_news_reads_the_recorded_feed() -> None:
    with fake_web({NEWS: fixture_text("google_news_wildfire.xml")}) as calls:
        reply = headlines("wildfire BC", CANADA)
    assert reply == (
        "1) \N{LEFT SINGLE QUOTATION MARK}God cleaned the slate\N{RIGHT SINGLE QUOTATION MARK}: "
        "Ashes pave the road to recovery after B.C. wildfires - Global News. 2) LETTER: BC "
        "Wildfire Service leadership questioned - Kelowna Capital News. 3) Growing wildfire "
        "burning out of control on Vancouver Island - CTV News."
    )
    assert calls == [("https://news.google.com/rss/search", {"q": "wildfire BC", **CANADA})]
    with fake_web({NEWS: fixture_text("google_news_wildfire.xml")}) as calls:
        headlines("", CANADA)
    assert calls[0] == ("https://news.google.com/rss", CANADA)


def test_news_with_a_broken_or_empty_feed() -> None:
    with fake_web({NEWS: "<rss><channel><item><title>cut off"}), pytest.raises(SourceError):
        headlines("x", CANADA)
    with fake_web({NEWS: "<rss><channel></channel></rss>"}):
        assert headlines("x", CANADA) is None


def test_news_edition_reads_news_region() -> None:
    assert edition("CA") == CANADA
    assert edition(" gb ") == {"hl": "en-GB", "gl": "GB", "ceid": "GB:en"}
    assert edition("CA:fr") == {"hl": "fr-CA", "gl": "CA", "ceid": "CA:fr"}
    for bad in ("Canada", "en-CA", "C:fr", "CA:french"):
        with pytest.raises(ValueError, match=f"NEWS_REGION is {bad!r}"):
            edition(bad)
