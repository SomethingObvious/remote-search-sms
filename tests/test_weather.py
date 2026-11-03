from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import pytest
from conftest import OPEN_METEO, OPEN_METEO_GEOCODE, fake_web, load

from remotesearch.commands.weather import ec_alerts, forecast, nws_alerts, sun, weather
from remotesearch.net import SourceError

EC = "https://api.weather.gc.ca/"
NWS = "https://api.weather.gov/"
# Before the recorded alerts expire and before the recorded tides, on a Sunday in Tofino.
NOW = datetime(2026, 9, 27, 17, 0, tzinfo=UTC)

TOFINO = load("open_meteo_geocode_tofino.json")


def place(name: str, lat: float, lon: float, admin1: str, country: str = "CA") -> dict[str, Any]:
    hit = {"name": name, "latitude": lat, "longitude": lon, "admin1": admin1}
    return {"results": [hit | {"country_code": country, "timezone": "America/Vancouver"}]}


@pytest.fixture(autouse=True)
def _clock() -> Any:
    with patch("remotesearch.net.now", return_value=NOW):
        yield


def test_weather_reads_the_recorded_conditions_and_alerts() -> None:
    responses = {
        OPEN_METEO_GEOCODE: TOFINO,
        OPEN_METEO: load("open_meteo_current.json"),
        EC: load("ec_alerts.json"),
    }
    with fake_web(responses) as calls:
        assert weather("in Tofino") == (
            "Tofino, British Columbia, CA: clear, 12C (feels 11C), wind 1km/h, humidity 78%. "
            "ALERT: frost advisory"
        )
    assert calls[0][1]["name"] == "Tofino"  # "in" is dropped before geocoding
    assert calls[2][1]["bbox"] == "-125.9174,49.1431,-125.8974,49.1631"


def test_weather_without_alerts_or_outside_canada() -> None:
    current = load("open_meteo_current.json")
    with fake_web({OPEN_METEO_GEOCODE: TOFINO, OPEN_METEO: current, EC: {"features": []}}):
        assert weather("Tofino") == (
            "Tofino, British Columbia, CA: clear, 12C (feels 11C), wind 1km/h, humidity 78%"
        )
    seattle = place("Seattle", 47.6, -122.3, "Washington", "US")
    with fake_web({OPEN_METEO_GEOCODE: seattle, OPEN_METEO: current}) as calls:
        assert weather("Seattle") == (
            "Seattle, Washington, US: clear, 12C (feels 11C), wind 1km/h, humidity 78%"
        )
    assert not any(url.startswith(EC) for url, _ in calls)


def test_weather_still_answers_when_the_alerts_are_down() -> None:
    responses = {
        OPEN_METEO_GEOCODE: TOFINO,
        OPEN_METEO: load("open_meteo_current.json"),
        EC: SourceError("Environment Canada", "it timed out"),
    }
    with fake_web(responses):
        assert weather("Tofino") == (
            "Tofino, British Columbia, CA: clear, 12C (feels 11C), wind 1km/h, humidity 78%. "
            "Couldn't check for weather alerts"
        )


def test_weather_for_an_unknown_place() -> None:
    with fake_web({OPEN_METEO_GEOCODE: {"generationtime_ms": 0.1}}):
        assert weather("Zzqxville") is None


def test_ec_alerts_only_in_force() -> None:
    alerts = load("ec_alerts.json")
    tofino = TOFINO["results"][0]
    with fake_web({EC: alerts}):
        assert ec_alerts(tofino) == ["frost advisory"]
    with (
        fake_web({EC: alerts}),
        patch("remotesearch.net.now", return_value=datetime(2026, 9, 29, tzinfo=UTC)),
    ):
        assert ec_alerts(tofino) == []


def test_forecast_reads_three_recorded_days() -> None:
    responses = {
        OPEN_METEO_GEOCODE: TOFINO,
        OPEN_METEO: load("open_meteo_daily.json"),
        EC: {"features": []},
    }
    with fake_web(responses) as calls:
        assert forecast("Tofino") == (
            "Tofino, British Columbia, CA: Sun overcast 15/6C, 2% 0mm, wind 15km/h. "
            "Mon heavy rain 14/10C, 100% 41mm, wind 27km/h. "
            "Tue light rain 15/10C, 96% 10mm, wind 23km/h"
        )
    assert calls[1][1]["forecast_days"] == 3


def test_forecast_stops_at_missing_days() -> None:
    daily = load("open_meteo_daily.json")
    daily["daily"]["temperature_2m_max"][1] = None
    daily["daily"]["precipitation_probability_max"][0] = 0
    with fake_web({OPEN_METEO_GEOCODE: TOFINO, OPEN_METEO: daily, EC: {"features": []}}):
        assert forecast("Tofino") == (
            "Tofino, British Columbia, CA: Sun overcast 15/6C, wind 15km/h"
        )
    with fake_web({OPEN_METEO_GEOCODE: TOFINO, OPEN_METEO: {"daily": {}}}):
        assert forecast("Tofino") is None


def test_sun_names_the_zone_and_skips_polar_days() -> None:
    responses = {OPEN_METEO_GEOCODE: TOFINO, OPEN_METEO: load("open_meteo_sun.json")}
    with fake_web(responses):
        assert sun("Tofino") == "Tofino, British Columbia, CA: sunrise 07:17, sunset 19:10 (PDT)"
    polar = load("open_meteo_sun.json")
    polar["daily"] = {"sunrise": [None], "sunset": [None]}
    with fake_web({OPEN_METEO_GEOCODE: TOFINO, OPEN_METEO: polar}):
        assert sun("Tofino") is None


def test_us_alerts_come_from_the_weather_service() -> None:
    cocoa = {"name": "Cocoa", "latitude": 28.0, "longitude": -80.6, "country_code": "US"}
    with fake_web({NWS: load("nws_alerts_florida.json")}) as calls:
        assert nws_alerts(cocoa) == ["flood warning", "rip current statement"]
    assert calls == [("https://api.weather.gov/alerts/active", {"point": "28.0000,-80.6000"})]
    # Each country only asks its own service.
    with fake_web({}) as calls:
        assert nws_alerts(TOFINO["results"][0]) == []
        assert ec_alerts(cocoa) == []
    assert calls == []
