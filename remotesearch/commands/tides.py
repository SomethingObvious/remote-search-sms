"""The next highs and lows from the nearest Canadian or US tide station, or a tide model."""

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from functools import cache
from typing import Any, NamedTuple
from zoneinfo import ZoneInfo

from .. import net
from ..places import find, km_between, place_label
from . import command

logger = logging.getLogger("remotesearch")

IWLS = "https://api-iwls.dfo-mpo.gc.ca/api/v1"
NOAA_STATIONS = "https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations.json"
NOAA_DATA = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
MARINE = "https://marine-api.open-meteo.com/v1/marine"
TIDE_RANGE_KM = 100  # past this, the nearest station's tides say little about the place
MODEL_RANGE_KM = 25  # the model's grid is about 8km, so further off is another stretch of water
WINDOW = timedelta(hours=25)  # a day of tides is three or four turns


class Station(NamedTuple):
    source: str  # "chs" or "noaa"
    id: str
    name: str
    lat: float
    lon: float


class Tide(NamedTuple):
    when: datetime
    height: float
    high: bool


# The station lists only change when a station opens or closes, so each is fetched once
# per run. The tide times themselves are fetched fresh every time.
@cache
def chs_stations() -> tuple[Station, ...]:
    found = net.get_json(
        f"{IWLS}/stations", "the tide service", params={"time-series-code": "wlp-hilo"}
    )
    return tuple(
        Station(
            "chs",
            s["id"],
            s.get("officialName", "the nearest station"),
            s["latitude"],
            s["longitude"],
        )
        for s in found or []
        if isinstance(s, dict) and "latitude" in s
    )


@cache
def noaa_stations() -> tuple[Station, ...]:
    found = net.get_json(NOAA_STATIONS, "NOAA Tides", params={"type": "tidepredictions"})
    return tuple(
        Station("noaa", s["id"], s.get("name", "the nearest station"), s["lat"], s["lng"])
        for s in (found or {}).get("stations") or []
        if isinstance(s, dict) and "lat" in s
    )


def chs_tides(station: Station, now: datetime) -> list[Tide]:
    events = net.get_json(
        f"{IWLS}/stations/{station.id}/data",
        "the tide service",
        params={
            "time-series-code": "wlp-hilo",
            "from": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "to": (now + WINDOW).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    )
    events = [e for e in events or [] if isinstance(e, dict) and "value" in e]
    tides = []
    for i, event in enumerate(events):
        # The series is only times and heights, so a high is one above its neighbour.
        other = events[i + 1] if i + 1 < len(events) else events[i - 1]
        when = datetime.fromisoformat(event["eventDate"])
        tides.append(Tide(when, event["value"], event["value"] > other["value"]))
    return tides


def noaa_tides(station: Station, now: datetime) -> list[Tide]:
    data = net.get_json(
        NOAA_DATA,
        "NOAA Tides",
        params={
            "product": "predictions",
            "interval": "hilo",
            "station": station.id,
            "begin_date": now.strftime("%Y%m%d %H:%M"),
            "range": int(WINDOW.total_seconds() // 3600),
            "datum": "MLLW",  # chart datum, the same zero the Canadian heights use
            "units": "metric",
            "time_zone": "gmt",
            "format": "json",
            "application": "remote-search-sms",
        },
    )
    tides = []
    for p in (data or {}).get("predictions") or []:
        when = datetime.strptime(p["t"], "%Y-%m-%d %H:%M").replace(tzinfo=now.tzinfo)
        tides.append(Tide(when, float(p["v"]), p.get("type") == "H"))
    return tides


SOURCES: dict[
    str, tuple[Callable[[], tuple[Station, ...]], Callable[[Station, datetime], list[Tide]]]
] = {
    "chs": (chs_stations, chs_tides),
    "noaa": (noaa_stations, noaa_tides),
}
# Each country's own service first, then its neighbour's, for a town near the border.
ORDER = {"CA": ("chs", "noaa"), "US": ("noaa", "chs")}


def nearest(hit: dict[str, Any]) -> tuple[Station, float] | None:
    lat, lon = hit["latitude"], hit["longitude"]
    for source in ORDER.get(hit.get("country_code", ""), ()):
        known = SOURCES[source][0]()
        if not known:
            continue
        station = min(known, key=lambda s: km_between(lat, lon, s.lat, s.lon))
        away = km_between(lat, lon, station.lat, station.lon)
        if away <= TIDE_RANGE_KM:
            return station, away
    return None


def modelled(hit: dict[str, Any], now: datetime) -> list[Tide] | None:
    """Highs and lows from Open-Meteo's tide model, or None where it has no sea."""
    data = net.get_json(
        MARINE,
        "Open-Meteo's tide model",
        params={
            "latitude": hit["latitude"],
            "longitude": hit["longitude"],
            "hourly": "sea_level_height_msl",
            "forecast_days": 3,
            "timezone": "UTC",
        },
    )
    if not isinstance(data, dict) or "latitude" not in data:
        return None
    if (
        km_between(hit["latitude"], hit["longitude"], data["latitude"], data["longitude"])
        > MODEL_RANGE_KM
    ):
        return None
    hourly = data.get("hourly") or {}
    times = [datetime.fromisoformat(t).replace(tzinfo=now.tzinfo) for t in hourly.get("time") or []]
    heights = hourly.get("sea_level_height_msl") or []
    tides = []
    for i in range(1, min(len(times), len(heights)) - 1):
        before, here, after = heights[i - 1], heights[i], heights[i + 1]
        if None in (before, here, after):
            continue
        high = here > before and here >= after
        if not high and not (here < before and here <= after):
            continue
        # The hourly values only bracket the turn, so a parabola through the three
        # around it finds the time between them.
        curve = before - 2 * here + after
        shift = (before - after) / (2 * curve) if curve else 0.0
        when = times[i] + timedelta(hours=shift)
        if now <= when <= now + WINDOW:
            tides.append(Tide(when, here - (before - after) * shift / 4, high))
    return tides


def listed(tides: list[Tide], zone: ZoneInfo, now: datetime, *, rounded: bool = False) -> str:
    today = now.astimezone(zone).date()
    shown = []
    for t in tides:
        when = t.when.astimezone(zone)
        if rounded:  # the model is only good to the hour, so its exact minutes would mislead
            when = datetime.fromtimestamp(round(when.timestamp() / 600) * 600, zone)
        day = "" if when.date() == today else f"{when:%a} "
        shown.append(f"{'high' if t.high else 'low'} {day}{when:%H:%M} {t.height:.1f}m")
    return ", ".join(shown)


@command(
    "tide",
    "tides",
    help="tide <place>: the next highs and lows at the nearest Canadian or US tide station, "
    "or from a tide model anywhere else, like 'tide Tofino'.",
    loose=True,
)
def tide(place: str) -> str | None:
    hit = find(place)
    if not hit:
        return None
    now = net.now().replace(microsecond=0)
    zone = ZoneInfo(hit.get("timezone") or "UTC")
    found = nearest(hit)
    if found:
        station, away = found
        tides = SOURCES[station.source][1](station, now)
        if len(tides) < 2:  # a 25-hour window always has three or four, so this is a gap
            return None
        where = station.name
        if away >= 5:
            where += f" ({away:.0f}km away)"
        return f"{where}: {listed(tides, zone, now)} ({now.astimezone(zone):%Z})"

    model = modelled(hit, now)
    if not model:
        return f"There's no tide station or tide model covering {place_label(hit)}."
    return (
        f"{place_label(hit)} has no tide station nearby, so this is Open-Meteo's tide model, "
        f"which can be an hour off: {listed(model, zone, now, rounded=True)} "
        f"({now.astimezone(zone):%Z}, heights from mean sea level)"
    )
