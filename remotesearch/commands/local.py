"""The local time somewhere, and a business's hours from OpenStreetMap."""

from datetime import datetime
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo

from .. import net
from ..places import find, place_label
from ..text import looks_relevant
from . import command


@command("time", help="time <place>: the local time there, like 'time Tokyo'.", loose=True)
def local_time(place: str) -> str | None:
    hit = find(place)
    if not hit or not hit.get("timezone"):
        return None
    now = datetime.now(ZoneInfo(hit["timezone"]))
    return f"{place_label(hit)}: {now:%H:%M %a %b} {now.day} ({now:%Z})"


# Nominatim's usage policy asks for no more than one request a second, and for
# results to be kept rather than fetched again.
@lru_cache(maxsize=128)
def nominatim(query: str) -> tuple[dict[str, Any], ...]:
    found = net.get_json(
        "https://nominatim.openstreetmap.org/search",
        "OpenStreetMap's Nominatim",
        params={"q": query, "format": "jsonv2", "extratags": 1, "addressdetails": 1, "limit": 3},
        gap=1.0,
    )
    return tuple(r for r in found or [] if isinstance(r, dict))


def street_address(address: dict[str, str]) -> str:
    street = " ".join(p for p in (address.get("house_number"), address.get("road")) if p)
    town = next((address[k] for k in ("city", "town", "village", "hamlet") if k in address), "")
    return ", ".join(p for p in (street, town) if p)


@command(
    "hours",
    "phone",
    "address",
    help="hours <business> <town>: opening hours, address and phone number from "
    "OpenStreetMap, like 'hours Tim Hortons Hope'.",
    loose=True,
)
def hours(query: str) -> str | None:
    # Nominatim reads "Tim Hortons, Hope" better than "Tim Hortons in Hope".
    wanted = query.replace(" in ", ", ").replace(" near ", ", ")
    for hit in nominatim(wanted):
        name = hit.get("name") or ""
        # A town or a boundary matching the town in the question is no use for its hours.
        if hit.get("category") in ("boundary", "place") or not name:
            continue
        if not looks_relevant(wanted.split(",")[0], name):
            continue
        tags = hit.get("extratags") or {}
        parts = [f"{name}, {street_address(hit.get('address') or {})}".rstrip(", ")]
        opening = tags.get("opening_hours")
        parts.append(f"Open {opening}" if opening else "No hours listed")
        phone = tags.get("phone") or tags.get("contact:phone")
        if phone:
            parts.append(phone)
        return ". ".join(parts)
    return None
