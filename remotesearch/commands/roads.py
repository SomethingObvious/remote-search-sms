"""Road events from DriveBC and the other 511 sites, and driving times from OSRM."""

import re
from typing import Any

from .. import net, road511
from ..places import find, place_label
from ..road511 import BC, QC, SITES, region
from . import command

# What people call BC highways, as opposed to the numbers DriveBC files them under.
HIGHWAY_NAMES = {
    "sea to sky": "99",
    "duffey lake": "99",
    "coquihalla": "5",
    "coq": "5",
    "trans canada": "1",
    "trans-canada": "1",
    "malahat": "1",
    "crowsnest": "3",
    "hope princeton": "3",
    "hope-princeton": "3",
    "yellowhead": "16",
    "okanagan connector": "97C",
    "connector": "97C",
}
LAST_UPDATE = re.compile(r"\s*(?:Last|Next) update: [^.]*\.")
DRIVE_USAGE = "Text it as drive <place> to <place>, like 'drive Vancouver to Whistler'."


# A route number, and what follows it, as in "hwy 99", "I-75 GA" or "A-73 in Quebec".
HIGHWAY = re.compile(
    r"(?:hwy|highway|route|rte|us|sr|i|a|r)?[\s#.-]*(\d{1,4}[a-z]?)(?:\s*,\s*|\s+in\s+|\s+)?(.*)"
)


def worst_first(event: dict[str, Any]) -> tuple[bool, bool, bool]:
    text = str(event.get("description", "")).lower()
    return (
        event.get("event_type") != "INCIDENT",
        event.get("severity") != "MAJOR",
        "closed" not in text,
    )


@command(
    "road",
    "roads",
    help="road <town or highway>: closures and events worst first, from DriveBC or the "
    "province's or state's 511, like 'road Squamish' or 'road hwy 2 AB'.",
    loose=True,
)
def road(text: str) -> str | None:
    name = text.lower().strip(" .")
    if name in HIGHWAY_NAMES:
        return drivebc(
            f"Highway {HIGHWAY_NAMES[name]}", {"road_name": f"Highway {HIGHWAY_NAMES[name]}"}
        )
    number = HIGHWAY.fullmatch(name)
    if number:
        route, rest = number.group(1).upper(), number.group(2).strip()
        # A bare number is a BC highway, as it always was.
        area = region(rest) if rest else BC
        if area == BC:
            return drivebc(f"Highway {route}", {"road_name": f"Highway {route}"})
        if area == QC:
            return road511.quebec(number=route)
        if area in SITES:
            return road511.on_highway(SITES[area], route)
        # Otherwise the number is part of a place, like "100 Mile House".
    hit = find(text)
    if not hit:
        return None
    area = (hit.get("country_code", ""), hit.get("admin1", ""))
    if area == BC:
        lat, lon = hit["latitude"], hit["longitude"]
        box = f"{lon - 0.3:.4f},{lat - 0.2:.4f},{lon + 0.3:.4f},{lat + 0.2:.4f}"
        return drivebc(f"near {hit['name']}", {"bbox": box})
    if area == QC:
        return road511.quebec(hit=hit)
    if area in SITES:
        return road511.near_place(SITES[area], hit)
    return (
        f"There's no road event feed for {place_label(hit)}. It covers BC, Quebec, most "
        "other provinces and 17 states."
    )


def drivebc(label: str, filters: dict[str, Any]) -> str:
    data = net.get_json(
        "https://api.open511.gov.bc.ca/events",
        "DriveBC",
        params={"format": "json", "status": "ACTIVE", "limit": 100, **filters},
    )
    events = sorted((data or {}).get("events") or [], key=worst_first)
    if not events:
        return f"No DriveBC events {label.replace('Highway', 'on Highway')} right now."
    notes = [LAST_UPDATE.sub("", str(e.get("description", ""))).strip() for e in events]
    count = f"{len(events)} event{'s' if len(events) != 1 else ''}"
    return f"DriveBC, {label}, {count}: " + " / ".join(n for n in notes if n)


def duration(seconds: float) -> str:
    hours, minutes = divmod(round(seconds / 60), 60)
    return f"{hours} h {minutes} min" if hours else f"{minutes} min"


@command(
    "drive",
    help="drive <place> to <place>: driving distance and time from OSRM, which knows nothing "
    "about traffic or closures.",
    loose=True,
)
def drive(text: str) -> str | None:
    if " to " not in text:
        return DRIVE_USAGE
    start, end = (p.strip() for p in text.removeprefix("from ").rsplit(" to ", 1))
    a, b = find(start), find(end)
    if not a or not b:
        return None
    route = net.get_json(
        f"https://router.project-osrm.org/route/v1/driving/"
        f"{a['longitude']},{a['latitude']};{b['longitude']},{b['latitude']}",
        "the OSRM router",
        params={"overview": "false"},
        gap=1.0,  # the public server allows one request a second
    )
    routes = (route or {}).get("routes") or []
    waypoints = (route or {}).get("waypoints") or []
    # With no road nearby OSRM snaps to the closest one it has, which can be an ocean away.
    if not routes or any(w.get("distance", 0) > 5000 for w in waypoints):
        return f"There's no road route from {place_label(a)} to {place_label(b)}."
    best = routes[0]
    return (
        f"{place_label(a)} to {place_label(b)}: {best['distance'] / 1000:.0f}km, "
        f"about {duration(best['duration'])} driving (OSRM, no traffic or closures)"
    )
