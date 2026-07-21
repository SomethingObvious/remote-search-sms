"""Headlines from Google News, in the edition NEWS_REGION picks."""

import re
from typing import TYPE_CHECKING
from xml.etree import ElementTree

from .. import net
from ..net import SourceError
from . import command

if TYPE_CHECKING:
    from ..reply import Answerer

GOOGLE_NEWS = "https://news.google.com/rss"
DEFAULT_REGION = "CA"
MAX_HEADLINES = 8


def edition(region: str) -> dict[str, str]:
    """Google News's parameters for NEWS_REGION, a country code like GB, or CA:fr for French."""
    country, _, language = region.partition(":")
    country, language = country.strip().upper(), language.strip().lower() or "en"
    if not re.fullmatch(r"[A-Z]{2}", country) or not re.fullmatch(r"[a-z]{2,3}", language):
        raise ValueError(
            f"NEWS_REGION is {region!r}, which isn't a country code like CA, or CA:fr for "
            "the news in French."
        )
    return {"hl": f"{language}-{country}", "gl": country, "ceid": f"{country}:{language}"}


def headlines(topic: str, params: dict[str, str]) -> str | None:
    """Top headlines from one Google News edition, or the latest there on ``topic``."""
    url = GOOGLE_NEWS
    if topic:
        url, params = f"{GOOGLE_NEWS}/search", {"q": topic, **params}
    feed = net.get_text(url, "Google News", params=params)
    if not feed:
        return None
    try:
        # ElementTree never fetches external entities, and the expat under Python 3.11
        # and later caps entity expansion, so a hostile feed can't do much here.
        root = ElementTree.fromstring(feed)  # noqa: S314
    except ElementTree.ParseError:
        raise SourceError("Google News", "its feed wasn't readable") from None
    titles = [t.strip() for t in (i.findtext("title") for i in root.iter("item")) if t]
    if not titles:
        return None
    return " ".join(f"{n}) {t}." for n, t in enumerate(titles[:MAX_HEADLINES], 1))


@command(
    "news",
    help="news: the top headlines from Google News. news <topic>: the latest on that topic.",
    loose=True,
    bare=True,
)
def news(topic: str, answerer: "Answerer") -> str | None:
    return headlines(topic, edition(answerer.config.get("NEWS_REGION") or DEFAULT_REGION))
