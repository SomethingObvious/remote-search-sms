"""The commands a text can start with, each module here adding its own with @command.

A plugin is any .py file in PLUGINS_DIR that does the same. See plugins/example.py.
"""

import hashlib
import importlib.util
import inspect
import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
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
    folder: str = ""  # the plugin folder it came from, empty for the built-in ones

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


def load_plugins(folder: Path) -> None:
    """Import every .py file in ``folder``, so their @command functions register too."""
    where = str(folder.resolve())
    # Named by folder as well, so plugin.py in two folders doesn't load as one module.
    tag = hashlib.sha256(where.encode()).hexdigest()[:8]
    for path in sorted(folder.glob("*.py")):
        if path.name.startswith("_"):
            continue
        name = f"remotesearch_plugin_{tag}_{path.stem}"
        if name in sys.modules:
            continue
        spec = importlib.util.spec_from_file_location(name, path)
        if not spec or not spec.loader:
            continue
        module = importlib.util.module_from_spec(spec)
        registered = len(REGISTERED)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            del sys.modules[name]
            del REGISTERED[registered:]  # so a retry doesn't list its commands twice
            raise SystemExit(f"The plugin {path} failed to load: {exc}") from exc
        REGISTERED[registered:] = [replace(c, folder=where) for c in REGISTERED[registered:]]
        logger.debug("Loaded the plugin %s", path.name)


def available(plugins: Path | None = None) -> list[Command]:
    """The built-in commands, then the ones from the plugin folder ``plugins``."""
    where = ""
    if plugins and plugins.is_dir():
        load_plugins(plugins)
        where = str(plugins.resolve())
    # A folder that PLUGINS_DIR no longer names stays loaded, but its commands don't count.
    return [c for c in REGISTERED if not c.folder or c.folder == where]


def table(off: frozenset[str] = frozenset(), plugins: Path | None = None) -> dict[str, Command]:
    """Every command by each of its words, leaving out any that ``off`` names a word of."""
    found: dict[str, Command] = {}
    for entry in available(plugins):
        if off & set(entry.words):
            continue
        for word in entry.words:
            # A plugin can take over a built-in word, as it registers later.
            found[word] = entry
    return found


# Imported last, as each of them needs command() above, and in the order help lists them.
from . import weather, tides, avalanche, roads, calc, local  # noqa: E402, F401, I001
from . import translate, news, scores, reference, sites  # noqa: E402, F401
