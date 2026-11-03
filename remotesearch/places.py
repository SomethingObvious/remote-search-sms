"""Turning a town's name into coordinates, for every command that takes a place."""

import math
from collections.abc import Iterable
from functools import lru_cache
from typing import Any

from . import net
from .text import words

LEADING_PREPOSITION = ("in ", "at ", "for ", "near ")
# The postal codes for the states and provinces, as in "Portland OR".
REGION_CODES = {
    "al": "alabama", "ak": "alaska", "az": "arizona", "ar": "arkansas", "ca": "california",
    "co": "colorado", "ct": "connecticut", "de": "delaware", "dc": "district of columbia",
    "fl": "florida", "ga": "georgia", "hi": "hawaii", "id": "idaho", "il": "illinois",
    "in": "indiana", "ia": "iowa", "ks": "kansas", "ky": "kentucky", "la": "louisiana",
    "me": "maine", "md": "maryland", "ma": "massachusetts", "mi": "michigan", "mn": "minnesota",
    "ms": "mississippi", "mo": "missouri", "mt": "montana", "ne": "nebraska", "nv": "nevada",
    "nh": "new hampshire", "nj": "new jersey", "nm": "new mexico", "ny": "new york",
    "nc": "north carolina", "nd": "north dakota", "oh": "ohio", "ok": "oklahoma",
    "or": "oregon", "pa": "pennsylvania", "ri": "rhode island", "sc": "south carolina",
    "sd": "south dakota", "tn": "tennessee", "tx": "texas", "ut": "utah", "vt": "vermont",
    "va": "virginia", "wa": "washington", "wv": "west virginia", "wi": "wisconsin",
    "wy": "wyoming", "ab": "alberta", "bc": "british columbia", "mb": "manitoba",
    "nb": "new brunswick", "nl": "newfoundland and labrador", "ns": "nova scotia",
    "nt": "northwest territories", "nu": "nunavut", "on": "ontario",
    "pe": "prince edward island", "qc": "quebec", "sk": "saskatchewan", "yt": "yukon",
    "uk": "gb", "usa": "us",
}  # fmt: skip
REGION_KEYS = ("admin1", "country", "country_code")


def place_words(text: str) -> str:
    """Drop the "in" from "weather in Tofino", since the geocoder wants just the name."""
    lowered = text.lower()
    for word in LEADING_PREPOSITION:
        if lowered.startswith(word):
            return text[len(word) :].strip()
    return text.strip()


# Towns don't move, so each name is looked up once per run.
@lru_cache(maxsize=256)
def geocode(place: str) -> tuple[dict[str, Any], ...]:
    """Every town Open-Meteo's keyless geocoder has for a name, best match first."""
    data = net.get_json(
        "https://geocoding-api.open-meteo.com/v1/search",
        "Open-Meteo's geocoder",
        params={"name": place, "count": 20, "format": "json"},
    )
    return tuple((data or {}).get("results") or [])


def best(hits: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    # The geocoder can rank a peak or a pass first, like the Tofino in Italy or the Rogers
    # Pass in South Africa, so a town (GeoNames' PPL codes) comes before them.
    hits = list(hits)
    towns = (h for h in hits if str(h.get("feature_code") or "").startswith("PPL"))
    return next(towns, hits[0] if hits else None)


def in_region(hit: dict[str, Any], region: str) -> bool:
    """Whether a place is in a state, province or country given by name or code."""
    asked = " ".join(words(region))
    names = {asked, REGION_CODES.get(asked)}
    return any(" ".join(words(str(hit.get(k) or ""))) in names for k in REGION_KEYS)


def find(text: str) -> dict[str, Any] | None:
    """The place a command's text names, as in "in Tofino", "Paris, France" or "Portland OR"."""
    place = place_words(text)
    if not place:
        return None
    hit = best(geocode(place))
    if hit and words(str(hit.get("name") or "")) == words(place):
        return hit
    # The geocoder finds nothing for "Brighton UK" and Portland Meadows for "Portland ME",
    # so a name with more after it is also tried as a town and a region.
    split = place.split()
    splits = [(" ".join(split[:-n]), " ".join(split[-n:])) for n in (1, 2) if len(split) > n]
    if "," in place:
        name, _, region = place.rpartition(",")
        splits = [(name, region)]
    for name, region in splits:
        picked = best(h for h in geocode(name.strip()) if in_region(h, region))
        if picked:
            return picked
    return hit


def place_label(hit: dict[str, Any]) -> str:
    """Name a geocoded place closely enough that a wrong match is obvious."""
    parts = [hit.get("name"), hit.get("admin1"), hit.get("country_code")]
    return ", ".join(str(p) for p in parts if p)


def km_between(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(a))
