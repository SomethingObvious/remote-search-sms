from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import pytest
from conftest import OPEN_METEO_GEOCODE, fake_web, load

from remotesearch.commands.tides import tide

IWLS_STATIONS = "https://api-iwls.dfo-mpo.gc.ca/api/v1/stations"
IWLS_DATA = "https://api-iwls.dfo-mpo.gc.ca/api/v1/stations/"
NOAA_STATIONS = "https://api.tidesandcurrents.noaa.gov/mdapi/"
NOAA_DATA = "https://api.tidesandcurrents.noaa.gov/api/"
MARINE = "https://marine-api.open-meteo.com/"
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


def test_tides_at_the_nearest_station_in_local_time() -> None:
    responses = {
        OPEN_METEO_GEOCODE: TOFINO,
        IWLS_DATA: load("iwls_tofino_hilo.json"),
        IWLS_STATIONS: load("iwls_stations.json"),
    }
    with fake_web(responses) as calls:
        assert tide("Tofino") == (
            "Tofino: high 13:42 3.6m, low 20:07 0.6m, high Mon 02:17 3.3m, low Mon 08:06 1.1m (PDT)"
        )
    assert calls[2] == (
        "https://api-iwls.dfo-mpo.gc.ca/api/v1/stations/5cebf1e23d0f4a073c4bc07c/data",
        {
            "time-series-code": "wlp-hilo",
            "from": "2026-09-27T17:00:00Z",
            "to": "2026-09-28T18:00:00Z",
        },
    )


def test_tides_name_a_distant_station() -> None:
    bamfield = place("Bamfield", 48.83, -125.14, "British Columbia")
    responses = {
        OPEN_METEO_GEOCODE: bamfield,
        IWLS_DATA: load("iwls_tofino_hilo.json"),
        IWLS_STATIONS: load("iwls_stations.json"),
    }
    with fake_web(responses):
        reply = tide("Bamfield")
    assert reply is not None
    assert reply.startswith("Ucluelet (33km away): high 13:42 3.6m")


def test_tides_inland_and_with_too_little_data() -> None:
    calgary = place("Calgary", 51.05, -114.07, "Alberta")
    responses = {
        OPEN_METEO_GEOCODE: calgary,
        IWLS_STATIONS: load("iwls_stations.json"),
        NOAA_STATIONS: load("noaa_stations.json"),
        MARINE: load("open_meteo_marine_calgary.json"),
    }
    with fake_web(responses):
        assert (
            tide("Calgary")
            == "There's no tide station or tide model covering Calgary, Alberta, CA."
        )
    one = load("iwls_tofino_hilo.json")[:1]
    responses = {
        OPEN_METEO_GEOCODE: TOFINO,
        IWLS_DATA: one,
        IWLS_STATIONS: load("iwls_stations.json"),
    }
    with fake_web(responses):
        assert tide("Tofino") is None


def test_tide_stations_are_fetched_once_per_run() -> None:
    responses = {
        OPEN_METEO_GEOCODE: TOFINO,
        IWLS_DATA: load("iwls_tofino_hilo.json"),
        IWLS_STATIONS: load("iwls_stations.json"),
    }
    with fake_web(responses) as calls:
        tide("Tofino")
        tide("Tofino")
    urls = [url for url, _ in calls]
    assert urls.count(IWLS_STATIONS) == 1
    assert sum(url.endswith("/data") for url in urls) == 2  # tide times are never cached


def test_a_us_town_gets_its_nearest_noaa_station() -> None:
    seattle = place("Seattle", 47.61, -122.33, "Washington", "US")
    seattle["results"][0]["timezone"] = "America/Los_Angeles"
    responses = {
        OPEN_METEO_GEOCODE: seattle,
        NOAA_STATIONS: load("noaa_stations.json"),
        NOAA_DATA: load("noaa_seattle_hilo.json"),
    }
    now = datetime(2026, 11, 3, 17, 0, tzinfo=UTC)
    with patch("remotesearch.net.now", return_value=now), fake_web(responses) as calls:
        assert tide("Seattle") == (
            "SEATTLE (Madison St.), Elliott Bay: high 12:44 3.5m, low 19:22 1.4m, "
            "high Wed 00:30 2.5m, low Wed 06:21 0.7m (PST)"
        )
    # Washington's own stations come first, so the Canadian list is never fetched.
    assert [url for url, _ in calls] == [
        "https://geocoding-api.open-meteo.com/v1/search",
        "https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations.json",
        "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter",
    ]
    params = calls[2][1]
    assert (params["station"], params["begin_date"], params["datum"]) == (
        "9447130",
        "20261103 17:00",
        "MLLW",
    )


def test_anywhere_else_gets_the_tide_model_with_a_warning() -> None:
    brighton = place("Brighton", 50.83, -0.14, "England", "GB")
    brighton["results"][0]["timezone"] = "Europe/London"
    responses = {OPEN_METEO_GEOCODE: brighton, MARINE: load("open_meteo_marine_brighton.json")}
    now = datetime(2026, 11, 3, 12, 30, tzinfo=UTC)
    with patch("remotesearch.net.now", return_value=now), fake_web(responses) as calls:
        assert tide("Brighton") == (
            "Brighton, England, GB has no tide station nearby, so this is Open-Meteo's tide "
            "model, which can be an hour off: high 17:50 1.1m, low Wed 00:10 -2.4m, "
            "high Wed 06:30 1.4m, low Wed 12:50 -2.3m (GMT, heights from mean sea level)"
        )
    # Neither station list is fetched outside Canada and the US.
    assert len(calls) == 2
