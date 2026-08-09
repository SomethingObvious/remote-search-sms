from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import pytest
from conftest import OPEN_METEO_GEOCODE, fake_web, load

from remotesearch.commands.roads import DRIVE_USAGE, drive, road

OPEN511 = "https://api.open511.gov.bc.ca/"
OSRM = "https://router.project-osrm.org/"
# Before the recorded alerts expire and before the recorded tides, on a Sunday in Tofino.
NOW = datetime(2026, 9, 27, 17, 0, tzinfo=UTC)


def place(name: str, lat: float, lon: float, admin1: str, country: str = "CA") -> dict[str, Any]:
    hit = {"name": name, "latitude": lat, "longitude": lon, "admin1": admin1}
    return {"results": [hit | {"country_code": country, "timezone": "America/Vancouver"}]}


@pytest.fixture(autouse=True)
def _clock() -> Any:
    with patch("remotesearch.net.now", return_value=NOW):
        yield


def test_roads_on_a_numbered_highway() -> None:
    with fake_web({OPEN511: load("open511_highway_99.json")}) as calls:
        reply = road("hwy 99")
    assert reply == (
        "DriveBC, Highway 99, 3 events: Utility work from Highway 99, In Vancouver and W 70th "
        "Ave to In Vancouver and W 71 Ave. Starting Mon Aug 24 until Sat Oct 31. From 9:30 AM "
        "to 3:00 PM PDT, Monday to Thursday. Right lane closed. / Mowing from Highway 99, In "
        "Whistler and At Alpha Lake Road to In Squamish and 453m North of Squamish Nation Ped "
        "Overpass. Starting Mon Sep 28 until Fri Oct 2. From 7:30 AM to 3:30 PM PDT, weekdays. "
        "Shoulder closed. Slow moving vehicle operations. No work scheduled on Sept 30th. / "
        "Ditch maintenance from Highway 99, 413m Southeast of 90 Kilometre Post to At Brunswick "
        "North Bridge. Starting Thu Oct 1 until Thu Oct 1. Right lane blocked."
    )
    assert calls[0][1] == {
        "format": "json",
        "status": "ACTIVE",
        "limit": 100,
        "road_name": "Highway 99",
    }


@pytest.mark.parametrize(
    ("text", "highway"),
    [
        ("99", "Highway 99"),
        ("Highway 1", "Highway 1"),
        ("coquihalla", "Highway 5"),
        ("Sea to Sky", "Highway 99"),
        ("hwy 97c", "Highway 97C"),
    ],
)
def test_roads_by_number_or_by_name(text: str, highway: str) -> None:
    with fake_web({OPEN511: {"events": []}}) as calls:
        reply = road(text)
    assert calls[0][1]["road_name"] == highway
    assert reply == f"No DriveBC events on {highway} right now."


def test_roads_near_a_town_worst_first() -> None:
    squamish = place("Squamish", 49.7, -123.16, "British Columbia")
    events = load("open511_highway_99.json")
    events["events"].append(
        {"event_type": "INCIDENT", "severity": "MAJOR", "description": "Crash. Road closed."}
    )
    with fake_web({OPEN_METEO_GEOCODE: squamish, OPEN511: events}) as calls:
        reply = road("Squamish")
    assert reply is not None
    assert reply.startswith("DriveBC, near Squamish, 4 events: Crash. Road closed. / Utility work")
    assert calls[1][1]["bbox"] == "-123.4600,49.5000,-122.8600,49.9000"


def test_roads_outside_bc() -> None:
    banff = place("Banff", 51.18, -115.57, "Alberta")
    with fake_web({OPEN_METEO_GEOCODE: banff}) as calls:
        assert road("Banff") == "DriveBC only covers BC roads, and Banff, Alberta, CA isn't in BC."
    assert len(calls) == 1


def test_drive_reads_the_recorded_route() -> None:
    vancouver = place("Vancouver", 49.2827, -123.1207, "British Columbia")
    whistler = place("Whistler", 50.1163, -122.9574, "British Columbia")
    responses = {
        OPEN_METEO_GEOCODE: lambda url, params: (
            vancouver if params["name"] == "Vancouver" else whistler
        ),
        OSRM: load("osrm_vancouver_whistler.json"),
    }
    with fake_web(responses) as calls:
        assert drive("from Vancouver to Whistler") == (
            "Vancouver, British Columbia, CA to Whistler, British Columbia, CA: 122km, about 1 h "
            "59 min driving (OSRM, no traffic or closures)"
        )
    assert calls[2][0] == (
        "https://router.project-osrm.org/route/v1/driving/-123.1207,49.2827;-122.9574,50.1163"
    )


def test_drive_refuses_a_route_snapped_across_an_ocean() -> None:
    vancouver = place("Vancouver", 49.2827, -123.1207, "British Columbia")
    london = place("London", 51.5072, -0.1276, "England", "GB")
    responses = {
        OPEN_METEO_GEOCODE: lambda url, params: (
            vancouver if params["name"] == "Vancouver" else london
        ),
        OSRM: load("osrm_vancouver_london.json"),
    }
    with fake_web(responses):
        assert drive("Vancouver to London") == (
            "There's no road route from Vancouver, British Columbia, CA to London, England, GB."
        )


def test_drive_needs_two_places() -> None:
    with fake_web({}) as calls:
        assert drive("Vancouver") == DRIVE_USAGE
        assert drive("Vancouver to Zzqx") is None
    assert len(calls) == 2  # it never asked OSRM
