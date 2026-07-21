"""Wiktionary, Wikipedia, Stack Overflow, and DuckDuckGo's instant answers for the web search."""

import html
import logging
import re
import urllib.parse

from bs4 import BeautifulSoup

from .. import net
from ..text import inline_text, looks_relevant, strip_refs
from . import command

logger = logging.getLogger("remotesearch")

WIKTIONARY = "https://en.wiktionary.org/api/rest_v1/page/definition/"


def duckduckgo(query: str) -> str | None:
    """DuckDuckGo's Instant Answer, which only covers direct answers and topic abstracts."""
    data = net.get_json(
        "https://api.duckduckgo.com/",
        "DuckDuckGo",
        params={"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"},
    )
    if not isinstance(data, dict):
        return None
    for key in ("Answer", "AbstractText", "Definition"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    # Related topics come back for nearly anything, so they get the same title check
    # as Wikipedia. The title is the last part of the topic's URL.
    for topic in data.get("RelatedTopics", []):
        if not isinstance(topic, dict) or not topic.get("Text"):
            continue
        title = urllib.parse.unquote(str(topic.get("FirstURL", "")).rsplit("/", 1)[-1])
        if looks_relevant(query, title.replace("_", " ")):
            return str(topic["Text"]).strip()
    return None


@command(
    "wiki",
    help="wiki <topic>: the Wikipedia summary, named so a near miss is easy to spot.",
)
def wikipedia(query: str) -> str | None:
    """Top Wikipedia hit's lead summary, named and checked for relevance.

    The article title goes in the reply on purpose. It costs a few characters, and it's
    the only way the reader can tell a real answer from a near miss.
    """
    hits = net.get_json(
        "https://en.wikipedia.org/w/api.php",
        "Wikipedia",
        params={
            "action": "query",
            "list": "search",
            "srsearch": query,
            "srlimit": "1",
            "format": "json",
        },
    )
    results = (hits or {}).get("query", {}).get("search", [])
    if not results:
        return None

    title = results[0]["title"]
    if not looks_relevant(query, title):
        logger.info("Skipping Wikipedia's hit %r for %r as the title doesn't match", title, query)
        return None

    # safe="" matters for titles like "AC/DC", where a bare slash splits the path.
    path = urllib.parse.quote(title.replace(" ", "_"), safe="")
    summary = net.get_json(f"https://en.wikipedia.org/api/rest_v1/page/summary/{path}", "Wikipedia")
    if not summary or summary.get("type") == "disambiguation" or not summary.get("extract"):
        return None
    text = strip_refs(summary["extract"])
    # Most lead sentences open with the article name, so it's only added when they
    # don't. Every repeated character is one fewer character of answer.
    return text if text.lower().startswith(title.lower()) else f"{title}: {text}"


@command("define", "def", "dict", help="define <word>: the first meanings from Wiktionary.")
def define(word: str) -> str | None:
    """The first sense of the first one or two parts of speech in Wiktionary's English entry."""
    # Titles are case-sensitive, and "Albedo" only has a German entry, so a capitalized
    # word gets a second try in lower case.
    for term in dict.fromkeys((word, word.lower())):
        found = net.get_json(WIKTIONARY + urllib.parse.quote(term, safe=""), "Wiktionary")
        english = found.get("en") if isinstance(found, dict) else None
        if english:
            break
    else:
        return None
    senses = []
    for entry in english[:2]:
        glosses = (inline_text(d.get("definition", "")) for d in entry.get("definitions", []))
        gloss = next((g for g in glosses if g), None)
        if gloss:
            senses.append(f"({entry.get('partOfSpeech', '').lower()}) {gloss}")
    return " ".join(senses) or None


@command(
    "so",
    "stack",
    "stackoverflow",
    help="so <question>: the top Stack Overflow question and its best answer.",
    loose=True,
    listed=False,
)
def stackoverflow(query: str) -> str | None:
    """Top Stack Overflow question plus its highest-voted answer."""
    found = net.get_json(
        "https://api.stackexchange.com/2.3/search/advanced",
        "Stack Overflow",
        params={
            "order": "desc",
            "sort": "relevance",
            "q": query,
            "site": "stackoverflow",
            "pagesize": "1",
        },
    )
    items = (found or {}).get("items", [])
    if not items:
        return None
    question = items[0]
    # Stack Exchange sends titles HTML-escaped, so "&quot;" would go out in the text.
    title = html.unescape(question.get("title", ""))
    question_id = question.get("question_id")
    if not question_id:
        return title or None
    answers = net.get_json(
        f"https://api.stackexchange.com/2.3/questions/{question_id}/answers",
        "Stack Overflow",
        params={
            "order": "desc",
            "sort": "votes",
            "site": "stackoverflow",
            "pagesize": "1",
            "filter": "withbody",
        },
    )
    body_items = (answers or {}).get("items", [])
    if not body_items:
        return f"{title} (no answers yet)"
    body = BeautifulSoup(body_items[0].get("body", ""), "html.parser").get_text(" ", strip=True)
    # The space get_text puts between tags also lands before punctuation after </code>.
    body = re.sub(r"\s+([.,;:!?)])", r"\1", body)
    if not title.endswith(("?", ".", "!")):
        title += "."
    return f"{title} {body}"
