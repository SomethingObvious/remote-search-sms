"""Turning one text into one answer, and sending a long answer a page at a time."""

import logging
import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from . import ai, commands
from .commands.news import DEFAULT_REGION, edition
from .commands.reference import duckduckgo, wikipedia
from .net import SourceError
from .places import place_words
from .search import snippet_answer, web_results
from .state import State
from .text import MAX_QUERY_CHARS, STOPWORDS, paginate, sms_text, truncate, words

logger = logging.getLogger("remotesearch")

DEFAULT_SMS_CHARS = 300  # two GSM-7 segments hold 306
EMAIL_MAX_CHARS = 4000  # an email has room for the whole answer, so it isn't paged

HELP_WORDS = {"help", "?", "commands"}
EMPTY_TEXT = "Your text came through empty. Text help to see the commands."
NO_RESULT_TEXT = (
    "Couldn't find a good answer for '{target}'. Try a keyword instead of a whole "
    "question, or text help for the commands."
)
DOWN_TEXT = "{name} isn't answering right now ({why}). Try again in a few minutes."
NOTHING_MORE_TEXT = "There's nothing more to send. Text a new question, or help for the commands."
ONLINE_TEXT = "Remote search is running. Text help to see the commands."


@dataclass(frozen=True)
class Incoming:
    """One question from one sender, and the way its answer goes back to them."""

    sender: str
    text: str
    channel: str  # "sms" or "email", which decides how the answer is cut to size
    send: Callable[[str], bool]
    # Who a "more" for this answer comes from, when the answer goes somewhere other than
    # the sender. Gateway mail answered through Twilio gets its "more" from PHONE_TO.
    replies_to: str = ""


def down_text(exc: SourceError) -> str:
    return DOWN_TEXT.format(name=exc.name[:1].upper() + exc.name[1:], why=exc.why)


def attempt(source: Callable[[str], Any], arg: str) -> tuple[Any, SourceError | None]:
    """Call a source, returning its result and any outage instead of raising."""
    try:
        return source(arg), None
    except SourceError as exc:
        logger.warning("%s is down: %s", exc.name, exc.why)
        return None, exc
    except Exception as exc:  # a bug in one source still leaves the others to answer
        logger.warning("%s failed on %r: %s", getattr(source, "__name__", source), arg, exc)
        return None, None


class Answerer:
    """Turns one question into one answer, with no idea who asked or how it'll be sent.

    Commands that take a second parameter get this, for ``config``, ``model``,
    ``limit``, ``search()`` and ``site_search()``.
    """

    def __init__(self, config: dict[str, str] | None = None, limit: int = DEFAULT_SMS_CHARS):
        self.config = config or {}
        self.limit = limit
        self.model = ai.model_from_config(self.config)
        try:
            edition(self.config.get("NEWS_REGION") or DEFAULT_REGION)
        except ValueError as exc:
            raise SystemExit(str(exc)) from None
        self.commands = commands.table()

    def help_text(self) -> str:
        listed = dict.fromkeys(c.words[0] for c in self.commands.values() if c.listed)
        return f"Ask anything, or start with {' '.join(listed)}. help <word> for more"

    def answer(self, query: str) -> str:
        """The whole answer to one text, before it's cut to fit an SMS."""
        query = re.sub(r"\s+", " ", query).strip()[:MAX_QUERY_CHARS].strip()
        if not query:
            return EMPTY_TEXT
        first, _, rest = query.partition(" ")
        key, rest = first.lower().strip(":,"), rest.strip()
        if key in HELP_WORDS:
            if not rest:
                return self.help_text()
            asked = self.commands.get(rest.lower().strip(":,?"))
            if asked:
                return asked.help
            # "help me ..." is a real question, so it goes on to the search.

        command = self.commands.get(key)
        if not command or not (rest or command.bare) or self._reads_as_prose(command, rest):
            return self.web(query)
        result, down = attempt(lambda text: command(text, self), rest)
        if down:
            return down_text(down)
        if result:
            return f"{command.words[0]}: {result}"
        return self.web(rest or query, skip=command.run)

    @staticmethod
    def _reads_as_prose(command: commands.Command, rest: str) -> bool:
        following = words(place_words(rest))[:1]
        return command.loose and bool(following) and following[0] in STOPWORDS

    def web(self, query: str, skip: Callable[..., Any] | None = None) -> str:
        """Ask the instant answers, Wikipedia and a web search at once, and pick the best."""
        lookups = [s for s in (duckduckgo, wikipedia) if s != skip]
        sources = [*lookups, self.search]
        with ThreadPoolExecutor(max_workers=len(sources)) as pool:
            results = list(pool.map(lambda s: attempt(s, query), sources))
        # DuckDuckGo's instant answer beats Wikipedia, and both beat a search snippet.
        reference = [found for found, _ in results[:-1] if found]
        hits = results[-1][0] or []
        downs = [down for _, down in results if down]

        if self.model and (reference or hits):
            written = ai.answer_from_results(self.model, query, hits, reference, self.limit)
            if written:
                return f"ai: {written}"
        found = reference[0] if reference else snippet_answer(query, hits)
        if found:
            return f"web: {found}"
        if downs:
            return down_text(downs[0])
        # The question is shortened so the advice after it still fits in the reply.
        return NO_RESULT_TEXT.format(target=truncate(query, 60))

    def search(self, query: str) -> list[Any]:
        return web_results(query, self.config.get("BRAVE_API_KEY", ""))

    def site_search(self, site: str, query: str) -> str | None:
        """The best result for ``query`` from one site, written by the model when there is one."""
        hits = self.search(f"site:{site} {query}")
        if self.model and hits:
            written = ai.answer_from_results(self.model, query, hits, [], self.limit)
            if written:
                return written
        return snippet_answer(query, hits)

    def pages(self, query: str, channel: str = "sms") -> list[str]:
        """Every message the answer to ``query`` goes out as."""
        full = self.answer(query)
        if channel == "email":
            return [truncate(full, EMAIL_MAX_CHARS)]
        return paginate(sms_text(full), self.limit)


def is_more(text: str) -> bool:
    return text.strip().lower().rstrip(".!?") == "more"


class Responder:
    """Answers each sender, and keeps the rest of a long SMS answer for when they text "more"."""

    def __init__(self, answerer: Answerer, state: State | None) -> None:
        self.answerer = answerer
        self.state = state

    def reply(self, sender: str, text: str, channel: str = "sms") -> str:
        if is_more(text):
            page = self.state.next_page(sender) if self.state else None
            return page or NOTHING_MORE_TEXT
        pages = self.answerer.pages(text, channel)
        if self.state and channel == "sms":
            self.state.set_pages(sender, pages[1:])
        return pages[0]
