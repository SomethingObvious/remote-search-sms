"""A web search of one site, for the ones with no API a personal app can use."""

from typing import TYPE_CHECKING

from . import command

if TYPE_CHECKING:
    from ..reply import Answerer


# Reddit's API is closed to personal apps and Quora has none, so the answer is the
# thread's title and its search snippet, or the model's reading of them.
@command(
    "reddit",
    help="reddit <question>: the best matching thread from a web search of reddit.com. quora "
    "and youtube work the same way.",
)
def reddit(query: str, answerer: "Answerer") -> str | None:
    return answerer.site_search("reddit.com", query)


@command(
    "quora",
    help="quora <question>: the best matching answer from a web search of quora.com.",
    listed=False,
)
def quora(query: str, answerer: "Answerer") -> str | None:
    return answerer.site_search("quora.com", query)


@command(
    "youtube",
    help="youtube <question>: the best matching video from a web search of youtube.com.",
    listed=False,
)
def youtube(query: str, answerer: "Answerer") -> str | None:
    return answerer.site_search("youtube.com", query)


@command(
    "site",
    help="site <domain> <question>: a web search of one site, like 'site mec.ca return policy'.",
    loose=True,
)
def site(text: str, answerer: "Answerer") -> str | None:
    domain, _, query = text.partition(" ")
    if "." not in domain or not query.strip():
        return None
    return answerer.site_search(domain.lower().split("//")[-1].strip("/"), query)
