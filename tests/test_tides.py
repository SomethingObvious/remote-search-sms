from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import pytest
from conftest import OPEN_METEO_GEOCODE, fake_web, load

from remotesearch.commands.tides import tide

IWLS_STATIONS = "https://api-iwls.dfo-mpo.gc.ca/api/v1/stations"
IWLS_DATA = "https://api-iwls.dfo-mpo.gc.ca/api/v1/stations/"
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
    with fake_web({OPEN_METEO_GEOCODE: calgary, IWLS_STATIONS: load("iwls_stations.json")}):
        assert tide("Calgary") == (
            "There's no Canadian tide station within 100km of Calgary, Alberta, CA."
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
