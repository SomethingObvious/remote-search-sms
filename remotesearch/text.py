"""Cleaning up what comes in, telling whether a result matches, and fitting replies into SMS."""

import os
import re
import unicodedata

from bs4 import BeautifulSoup

MAX_QUERY_CHARS = 200  # anything longer is a signature or boilerplate, not a question
MOST_PAGES = 6  # nobody pages through a novel by SMS

# Rogers puts "Rogers MMS" in front of the text itself, but a bare "Rogers" only
# counts as noise on its own line, or "who is Fred Rogers" would lose its answer.
CARRIER_NOISE = re.compile(
    r"Rogers MMS|^\s*Rogers\s*$|This message is brought to you by[^\n]*|Sent from my \w+",
    re.IGNORECASE | re.MULTILINE,
)

# Words that carry no topic, so they never count as evidence that a result matches.
STOPWORDS = frozenset(
    """a an and are as at be been but by can did do does for from had has have how i if in
    is it its me my of on or that the their there these they this to was were what when
    where which who why will with you your""".split()  # noqa: SIM905 - reads better as prose
)


def clean_query(text: str) -> str:
    """Drop carrier boilerplate and collapse the rest to one line."""
    text = CARRIER_NOISE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def html_to_text(markup: str) -> str:
    return clean_query(BeautifulSoup(markup, "html.parser").get_text("\n"))


def inline_text(markup: str) -> str:
    """The text of an HTML snippet whose tags sit mid-sentence, with nothing added between them."""
    return re.sub(r"\s+", " ", BeautifulSoup(markup, "html.parser").get_text()).strip()


def strip_refs(text: str) -> str:
    """Remove reference markers like [2] or [note] from prose."""
    return re.sub(r"\[[A-Za-z0-9]+\]", "", text).strip()


def words(text: str) -> list[str]:
    """Lowercase words with their accents dropped, so "Forêt" matches "foret"."""
    plain = unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode("ascii")
    return re.findall(r"[a-z0-9]+", plain)


def content_words(text: str) -> set[str]:
    """Lowercase words of three or more letters that aren't stopwords."""
    return {w for w in words(text) if len(w) >= 3 and w not in STOPWORDS}


def _shares_stem(a: str, b: str) -> bool:
    if a == b:
        return True
    return len(os.path.commonprefix([a, b])) >= 4  # purify and purified, not hike and hiking


def looks_relevant(query: str, title: str) -> bool:
    """True when a result's title overlaps what was asked.

    Wikipedia's search returns a hit for any query built from real words, so without
    this "how do I treat a blister" gets a summary of a memoir that mentions blisters.
    On a trail with no data, a confident wrong answer is worse than none.
    """
    # A query made only of short words ("AC/DC", "UV") still has to match something.
    asked = content_words(query) or set(words(query))
    return any(_shares_stem(t, q) for t in words(title) for q in asked)


def truncate(text: str, limit: int) -> str:
    """Trim to at most ``limit`` characters on a word boundary, ending in a plain '...'."""
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    if limit <= 3:  # no room for the dots, and text[:limit - 3] would slice from the end
        return text[: max(limit, 0)]
    cut = text[: limit - 3]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip() + "..."


GSM7_BASIC = frozenset(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
)
GSM7_EXTENSION = frozenset("^{}\\[~]|€\f")

# One character outside GSM-7 makes Twilio send the whole reply as UCS-2, where a
# segment holds 67 characters instead of 153, so a 300-character answer bills as 5.
# The brackets and the rest of GSM-7's extension table cost two characters each, so
# they're swapped too, which keeps a 300-character reply at two segments.
SMS_PUNCTUATION = str.maketrans(
    {
        "\N{LEFT SINGLE QUOTATION MARK}": "'",
        "\N{RIGHT SINGLE QUOTATION MARK}": "'",
        "\N{LEFT DOUBLE QUOTATION MARK}": '"',
        "\N{RIGHT DOUBLE QUOTATION MARK}": '"',
        "\N{MINUS SIGN}": "-",
        "`": "'",
        "[": "(",
        "]": ")",
        "{": "(",
        "}": ")",
        "~": "-",
        "|": "/",
        "\\": "/",
        "\r": "",
    }
)


def sms_text(text: str) -> str:
    """Fold text into GSM-7, keeping the accents it has (like é) and dropping the rest.

    Text with letters that have no GSM-7 form, like Japanese or Hindi, is sent as it is
    instead, as folding would empty it. That bills as UCS-2, but an answer beats none.
    """
    text = text.translate(SMS_PUNCTUATION)
    out = []
    for c in text:
        if c in GSM7_BASIC:
            out.append(c)
        elif unicodedata.category(c) == "Pd":  # every dash Unicode knows of
            out.append("-")
        else:
            plain = unicodedata.normalize("NFKD", c).encode("ascii", "ignore").decode("ascii")
            if not plain and unicodedata.category(c).startswith("L"):
                return text
            out.append(plain)
    return "".join(out)


def sms_segments(text: str) -> int:
    """How many SMS a carrier bills ``text`` as, going by its encoding and length."""
    if all(c in GSM7_BASIC or c in GSM7_EXTENSION for c in text):
        units = len(text) + sum(c in GSM7_EXTENSION for c in text)
        single, multi = 160, 153
    else:
        # UCS-2 counts UTF-16 code units, so an emoji takes two.
        units = len(text.encode("utf-16-le")) // 2
        single, multi = 70, 67
    return 1 if units <= single else -(-units // multi)


def more_marker(page: int, total: int) -> str:
    """What ends every page but the last, which says how many are left and how to get them."""
    return f" (more {page}/{total})"


def paginate(text: str, limit: int, most: int = MOST_PAGES) -> list[str]:
    """Split ``text`` into pages of at most ``limit`` characters, each but the last marked.

    Past ``most`` pages the last one is cut short with '...'.
    """
    text = re.sub(r"\s+", " ", text).strip()
    room = limit - len(more_marker(most, most))
    if room < 20:  # too small to page, so it's just cut
        return [truncate(text, limit)]
    pages: list[str] = []
    while len(text) > limit and len(pages) < most - 1:
        cut = text[:room]
        if " " in cut:
            cut = cut.rsplit(" ", 1)[0]
        pages.append(cut.rstrip())
        text = text[len(cut) :].lstrip()
    pages.append(truncate(text, limit))
    total = len(pages)
    return [p + more_marker(n, total) for n, p in enumerate(pages[:-1], 1)] + pages[-1:]
