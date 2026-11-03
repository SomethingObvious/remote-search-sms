"""Weather now, three days of forecast and the sun, from Open-Meteo, with Canadian and US alerts."""

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from .. import net
from ..net import SourceError
from ..places import find, place_label
from . import command

# Open-Meteo returns a WMO weather code, and this names it.
WMO_CODES = {
    0: "clear",
    1: "mainly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "fog",
    48: "freezing fog",
    51: "light drizzle",
    53: "drizzle",
    55: "heavy drizzle",
    56: "freezing drizzle",
    57: "freezing drizzle",
    61: "light rain",
    63: "rain",
    65: "heavy rain",
    66: "freezing rain",
    67: "freezing rain",
    71: "light snow",
    73: "snow",
    75: "heavy snow",
    77: "snow grains",
    80: "rain showers",
    81: "rain showers",
    82: "heavy rain showers",
    85: "snow showers",
    86: "heavy snow showers",
    95: "thunderstorm",
    96: "thunderstorm with hail",
    99: "thunderstorm with hail",
}


def open_meteo(hit: dict[str, Any], days: int = 1, **fields: str) -> dict[str, Any] | None:
    data = net.get_json(
        "https://api.open-meteo.com/v1/forecast",
        "Open-Meteo",
        params={
            "latitude": hit["latitude"],
            "longitude": hit["longitude"],
            "timezone": "auto",
            "forecast_days": days,
            **fields,
        },
    )
    return data if isinstance(data, dict) else None


def ec_alerts(hit: dict[str, Any]) -> list[str]:
    """Names of the Environment Canada alerts in force at a Canadian place."""
    if hit.get("country_code") != "CA":
        return []
    lat, lon = hit["latitude"], hit["longitude"]
    data = net.get_json(
        "https://api.weather.gc.ca/collections/weather-alerts/items",
        "Environment Canada",
        params={
            "f": "json",
            "bbox": f"{lon - 0.01:.4f},{lat - 0.01:.4f},{lon + 0.01:.4f},{lat + 0.01:.4f}",
            "skipGeometry": "true",
            "limit": 50,
        },
    )
    now = net.now()
    names: list[str] = []
    for feature in (data or {}).get("features") or []:
        props = feature.get("properties") or {}
        expires = props.get("expiration_datetime")
        if props.get("status_en") == "ended" or (expires and datetime.fromisoformat(expires) < now):
            continue
        name = props.get("alert_name_en")
        if name and name not in names:
            names.append(name)
    return names


# The National Weather Service covers the states and the territories, and answers 400
# for a point anywhere else.
NWS_PLACES = frozenset({"US", "PR", "VI", "GU", "AS", "MP"})


def nws_alerts(hit: dict[str, Any]) -> list[str]:
    """Names of the National Weather Service alerts in force at a US place."""
    if hit.get("country_code") not in NWS_PLACES:
        return []
    data = net.get_json(
        "https://api.weather.gov/alerts/active",
        "the National Weather Service",
        params={"point": f"{hit['latitude']:.4f},{hit['longitude']:.4f}"},
    )
    names: list[str] = []
    for feature in (data or {}).get("features") or []:
        props = feature.get("properties") or {}
        if props.get("status") != "Actual" or props.get("messageType") == "Cancel":
            continue
        name = str(props.get("event") or "").lower()
        if name and name not in names:
            names.append(name)
    return names


def with_alerts(hit: dict[str, Any], reply: str) -> str:
    try:
        alerts = ec_alerts(hit) + nws_alerts(hit)
    except SourceError:
        return f"{reply}. Couldn't check for weather alerts"
    return f"{reply}. ALERT: {', '.join(alerts)}" if alerts else reply


@command(
    "weather",
    help="weather <place>: current conditions and any weather alerts, like 'weather Tofino'. "
    "A region picks the right town, like 'weather Paris, France'.",
    loose=True,
)
def weather(place: str) -> str | None:
    hit = find(place)
    if not hit:
        return None
    data = open_meteo(
        hit,
        current="temperature_2m,apparent_temperature,relative_humidity_2m,wind_speed_10m,weather_code",
    )
    now = (data or {}).get("current")
    if not now:
        return None
    reply = (
        f"{place_label(hit)}: {WMO_CODES.get(now.get('weather_code'), 'unknown')}, "
        f"{now['temperature_2m']:.0f}C (feels {now['apparent_temperature']:.0f}C), "
        f"wind {now['wind_speed_10m']:.0f}km/h, humidity {now['relative_humidity_2m']:.0f}%"
    )
    return with_alerts(hit, reply)


@command(
    "forecast",
    help="forecast <place>: three days of highs, lows, rain and wind from Open-Meteo, plus "
    "any alerts in Canada or the US.",
    loose=True,
)
def forecast(place: str) -> str | None:
    hit = find(place)
    if not hit:
        return None
    data = open_meteo(
        hit,
        days=3,
        daily="weather_code,temperature_2m_max,temperature_2m_min,"
        "precipitation_probability_max,precipitation_sum,wind_speed_10m_max",
    )
    daily = (data or {}).get("daily") or {}
    days = []
    for i, date in enumerate(daily.get("time") or []):
        try:
            high, low = daily["temperature_2m_max"][i], daily["temperature_2m_min"][i]
            chance, rain = daily["precipitation_probability_max"][i], daily["precipitation_sum"][i]
            wind = daily["wind_speed_10m_max"][i]
            sky = WMO_CODES.get(daily["weather_code"][i], "unknown")
        except (KeyError, IndexError):
            break
        if high is None or low is None:
            break
        day = f"{datetime.fromisoformat(date):%a} {sky} {high:.0f}/{low:.0f}C"
        if chance:
            day += f", {chance:.0f}% {rain or 0:.0f}mm"
        days.append(f"{day}, wind {wind or 0:.0f}km/h")
    if not days:
        return None
    return with_alerts(hit, f"{place_label(hit)}: {'. '.join(days)}")


@command(
    "sun",
    "sunrise",
    "sunset",
    help="sun <place>: today's sunrise and sunset in that place's time zone.",
    loose=True,
)
def sun(place: str) -> str | None:
    hit = find(place)
    if not hit:
        return None
    data = open_meteo(hit, daily="sunrise,sunset") or {}
    daily = data.get("daily") or {}
    try:
        sunrise, sunset = daily["sunrise"][0], daily["sunset"][0]
    except (KeyError, IndexError):
        return None
    if not sunrise or not sunset:  # null above the Arctic Circle in midsummer and midwinter
        return None
    # Open-Meteo abbreviates Pacific time as GMT-7, so the name comes from zoneinfo.
    zone = f"{net.now().astimezone(ZoneInfo(hit['timezone'])):%Z}" if hit.get("timezone") else ""
    zone = zone or data.get("timezone_abbreviation", "local")
    return f"{place_label(hit)}: sunrise {sunrise[11:16]}, sunset {sunset[11:16]} ({zone})"
