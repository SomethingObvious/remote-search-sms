"""The next highs and lows from the Canadian Hydrographic Service."""

from datetime import datetime, timedelta
from functools import cache
from typing import Any
from zoneinfo import ZoneInfo

from .. import net
from ..places import find, km_between, place_label
from . import command

IWLS = "https://api-iwls.dfo-mpo.gc.ca/api/v1"
TIDE_RANGE_KM = 100  # past this, the nearest station's tides say little about the place


# The station list only changes when a station opens or closes, so it's fetched once
# per run. The tide times themselves are fetched fresh every time.
@cache
def stations() -> tuple[dict[str, Any], ...]:
    found = net.get_json(
        f"{IWLS}/stations", "the tide service", params={"time-series-code": "wlp-hilo"}
    )
    return tuple(s for s in found or [] if isinstance(s, dict) and "latitude" in s)


@command(
    "tide",
    "tides",
    help="tide <place>: the next highs and lows at the nearest Canadian Hydrographic Service "
    "station, like 'tide Tofino'.",
    loose=True,
)
def tide(place: str) -> str | None:
    hit = find(place)
    if not hit:
        return None
    known = stations()
    if not known:
        return None
    lat, lon = hit["latitude"], hit["longitude"]
    station = min(known, key=lambda s: km_between(lat, lon, s["latitude"], s["longitude"]))
    away = km_between(lat, lon, station["latitude"], station["longitude"])
    if away > TIDE_RANGE_KM:
        return f"There's no Canadian tide station within {TIDE_RANGE_KM}km of {place_label(hit)}."

    now = net.now().replace(microsecond=0)
    events = net.get_json(
        f"{IWLS}/stations/{station['id']}/data",
        "the tide service",
        params={
            "time-series-code": "wlp-hilo",
            "from": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "to": (now + timedelta(hours=25)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    )
    events = [e for e in events or [] if isinstance(e, dict) and "value" in e]
    if len(events) < 2:  # a 25-hour window always has three or four, so this is a gap
        return None
    zone = ZoneInfo(hit.get("timezone") or "UTC")
    today = now.astimezone(zone).date()
    tides = []
    for i, event in enumerate(events):
        # The series is only times and heights, so a high is one above its neighbour.
        other = events[i + 1] if i + 1 < len(events) else events[i - 1]
        kind = "high" if event["value"] > other["value"] else "low"
        when = datetime.fromisoformat(event["eventDate"]).astimezone(zone)
        day = "" if when.date() == today else f"{when:%a} "
        tides.append(f"{kind} {day}{when:%H:%M} {event['value']:.1f}m")
    where = station.get("officialName", "the nearest station")
    if away >= 5:
        where += f" ({away:.0f}km away)"
    return f"{where}: {', '.join(tides)} ({now.astimezone(zone):%Z})"
