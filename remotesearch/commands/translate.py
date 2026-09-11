"""Translation through the model when there is one, and MyMemory when there isn't."""

import re
from typing import TYPE_CHECKING

from .. import ai, net
from . import command

if TYPE_CHECKING:
    from ..reply import Answerer

USAGE = "Text it as translate <words> to <language>, like 'translate where is the bus to french'."

# MyMemory wants ISO 639-1 codes, and people text the language's name.
LANGUAGES = {
    "arabic": "ar",
    "chinese": "zh",
    "mandarin": "zh",
    "cantonese": "zh-HK",
    "croatian": "hr",
    "czech": "cs",
    "danish": "da",
    "dutch": "nl",
    "english": "en",
    "finnish": "fi",
    "french": "fr",
    "german": "de",
    "greek": "el",
    "hebrew": "he",
    "hindi": "hi",
    "hungarian": "hu",
    "icelandic": "is",
    "indonesian": "id",
    "italian": "it",
    "japanese": "ja",
    "korean": "ko",
    "norwegian": "no",
    "persian": "fa",
    "farsi": "fa",
    "polish": "pl",
    "portuguese": "pt",
    "punjabi": "pa",
    "romanian": "ro",
    "russian": "ru",
    "spanish": "es",
    "swedish": "sv",
    "tagalog": "tl",
    "thai": "th",
    "turkish": "tr",
    "ukrainian": "uk",
    "vietnamese": "vi",
}


def translation_request(text: str) -> tuple[str, str, str] | None:
    """Split "where is the bus to french" into the words, the language and its code."""
    match = re.fullmatch(r"(.+?)\s+(?:to|into)\s+([a-z]+)\.?", text.strip(), re.IGNORECASE)
    code = LANGUAGES.get(match.group(2).lower()) if match else None
    if not match or not code:
        return None
    return match.group(1), match.group(2).capitalize(), code


def mymemory(words: str, code: str) -> str | None:
    found = net.get_json(
        "https://api.mymemory.translated.net/get",
        "MyMemory",
        params={"q": words, "langpair": f"autodetect|{code}"},
    )
    found = found if isinstance(found, dict) else {}
    if found.get("quotaFinished"):
        raise net.SourceError("MyMemory", "it's out of free translations for today")
    if str(found.get("responseStatus")) != "200":
        raise net.SourceError("MyMemory", str(found.get("responseDetails") or "it sent an error"))
    return (found.get("responseData") or {}).get("translatedText") or None


@command(
    "translate",
    help="translate <words> to <language>, like 'translate where is the bus to french'.",
)
def translate(text: str, answerer: "Answerer") -> str | None:
    request = translation_request(text)
    if not request:
        return USAGE
    words, language, code = request
    result = ai.translate(answerer.model, words, language) if answerer.model else None
    result = result or mymemory(words, code)
    return f"{language}: {result}" if result else None
