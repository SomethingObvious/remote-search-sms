"""An example plugin. Every .py file in this folder is loaded at start, so this one is live.

Copy it to start your own. A command gets the text after its word and returns the
answer, or None to send the text on to a web search instead. Give the function a
second parameter to get the Answerer, with its config, search() and model.
"""

from datetime import datetime
from typing import Any

from remotesearch.commands import command


@command(
    "ping",
    help="ping: the time where it runs, to check that it's up.",
    bare=True,
    listed=False,
)
def ping(_text: str) -> str:
    return f"It's running, and it's {datetime.now():%H:%M %a} here."


@command(
    "note",
    help="note: whatever NOTE says in config.txt, like a campsite's booking line.",
    bare=True,
    listed=False,
)
def note(_text: str, answerer: Any) -> str:
    return answerer.config.get("NOTE") or "There's no note yet. Set NOTE in config.txt."
