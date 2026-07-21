"""Turning a town's name into coordinates, for every command that takes a place."""

import math
from functools import lru_cache
from typing import Any

from . import net

LEADING_PREPOSITION = ("in ", "at ", "for ", "near ")


def place_words(text: str) -> str:
    """Drop the "in" from "weather in Tofino", since the geocoder wants just the name."""
    lowered = text.lower()
    for word in LEADING_PREPOSITION:
        if lowered.startswith(word):
            return text[len(word) :].strip()
    return text.strip()


# Towns don't move, so each name is looked up once per run.
@lru_cache(maxsize=256)
def geocode(place: str) -> dict[str, Any] | None:
    """Resolve a place name to coordinates through Open-Meteo's keyless geocoder."""
    data = net.get_json(
        "https://geocoding-api.open-meteo.com/v1/search",
        "Open-Meteo's geocoder",
        params={"name": place, "count": 1, "format": "json"},
    )
    results = (data or {}).get("results") or []
    return results[0] if results else None


def find(text: str) -> dict[str, Any] | None:
    """The place a command's text names, as in "in Tofino" or "Paris, France"."""
    return geocode(place_words(text))


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
