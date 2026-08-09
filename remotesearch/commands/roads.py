"""DriveBC road events, and driving distance and time from OSRM."""

import re
from typing import Any

from .. import net
from ..places import find, place_label
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


def road_filter(text: str) -> tuple[str, dict[str, Any]] | None:
    """Turn "hwy 99", "coquihalla" or a BC town into a label and an Open511 filter."""
    name = text.lower().strip(" .")
    number = re.fullmatch(r"(?:hwy|highway|route|rte)?\s*#?\s*(\d{1,3}[a-z]?)", name)
    if number or name in HIGHWAY_NAMES:
        road = number.group(1).upper() if number else HIGHWAY_NAMES[name]
        return f"Highway {road}", {"road_name": f"Highway {road}"}
    hit = find(text)
    if not hit:
        return None
    if hit.get("admin1") != "British Columbia":
        return place_label(hit), {}
    lat, lon = hit["latitude"], hit["longitude"]
    box = f"{lon - 0.3:.4f},{lat - 0.2:.4f},{lon + 0.3:.4f},{lat + 0.2:.4f}"
    return f"near {hit['name']}", {"bbox": box}


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
    help="road <highway or BC town>: DriveBC closures and events, worst first, like "
    "'road hwy 99', 'road coquihalla' or 'road Squamish'.",
    loose=True,
)
def road(text: str) -> str | None:
    found = road_filter(text)
    if not found:
        return None
    label, filters = found
    if not filters:
        return f"DriveBC only covers BC roads, and {label} isn't in BC."
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
