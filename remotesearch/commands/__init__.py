"""The commands a text can start with, each module here adding its own with @command."""

import inspect
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("remotesearch")


@dataclass(frozen=True)
class Command:
    words: tuple[str, ...]  # the first one tags the reply and names it in help
    run: Callable[..., str | None]
    help: str
    # A word that also starts ordinary sentences, like "so" or "time", only counts as
    # a command when the next word isn't a stopword. "so what is lye" is a question.
    loose: bool = False
    bare: bool = False  # it works with nothing after it, like "news"
    listed: bool = True  # named in the plain help text, which has to fit one SMS
    wants_context: bool = False

    def __call__(self, text: str, context: Any) -> str | None:
        return self.run(text, context) if self.wants_context else self.run(text)


REGISTERED: list[Command] = []


def command(
    *words: str, help: str, loose: bool = False, bare: bool = False, listed: bool = True
) -> Callable[[Callable[..., str | None]], Callable[..., str | None]]:
    """Register a function as the command for ``words``.

    The function gets the rest of the text after the command word, and returns the
    answer or None when it has none, which sends the text on to a web search. A second
    parameter, if it has one, gets the Answerer, for its settings, search and model.
    """

    def register(run: Callable[..., str | None]) -> Callable[..., str | None]:
        needed = [
            p
            for p in inspect.signature(run).parameters.values()
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD) and p.default is p.empty
        ]
        wants = len(needed) > 1
        REGISTERED.append(Command(words, run, help, loose, bare, listed, wants))
        return run

    return register


def table() -> dict[str, Command]:
    """Every command by each of its words."""
    found: dict[str, Command] = {}
    for entry in REGISTERED:
        for word in entry.words:
            found[word] = entry
    return found


# Imported last, as each of them needs command() above, and in the order help lists them.
from . import weather, tides, avalanche, roads, calc, local  # noqa: E402, F401, I001
from . import translate, news, scores, reference  # noqa: E402, F401
