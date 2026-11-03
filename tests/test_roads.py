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


def test_roads_where_there_is_no_feed() -> None:
    portland = place("Portland", 45.52, -122.68, "Oregon", "US")
    with fake_web({OPEN_METEO_GEOCODE: portland}) as calls:
        assert road("Portland") == (
            "There's no road event feed for Portland, Oregon, US. It covers BC, Quebec, most "
            "other provinces and 17 states."
        )
    assert len(calls) == 1


def test_a_town_named_with_a_number_is_looked_up_as_a_town() -> None:
    town = place("100 Mile House", 51.64, -121.29, "British Columbia")
    with fake_web({OPEN_METEO_GEOCODE: town, OPEN511: {"events": []}}):
        assert road("100 Mile House") == "No DriveBC events near 100 Mile House right now."


ALBERTA = {
    f"https://511.alberta.ca/{path}/{layer}": load(f"alberta511_{layer.lower()}{suffix}.json")
    for layer in ("Incidents", "Closures")
    for path, suffix in (("List/GetData", ""), ("map/mapIcons", "_map"))
}


def test_roads_near_an_alberta_town() -> None:
    lake_louise = place("Lake Louise", 51.43, -116.18, "Alberta")
    with fake_web(ALBERTA | {OPEN_METEO_GEOCODE: lake_louise}):
        assert road("Lake Louise") == (
            "511 Alberta, near Lake Louise, 1 event: Traffic advisory on Moraine Lake Rd All "
            "Directions Near Tramline. All lanes closed. Activities: Road Closed Comments: "
            "Moraine Lake Road is CLOSED. For more information about this closure, visit the "
            'Parks Canada "Visiting Lake Louise and Moraine Lake" webpage link: '
            "https://parks.canada.ca/pn-np/ab/banff/visit/parkbus/louise"
        )


@pytest.mark.parametrize("text", ["hwy 6 ab", "6 alberta", "Highway 6, AB"])
def test_roads_on_a_highway_in_another_province(text: str) -> None:
    with fake_web(ALBERTA):
        assert road(text) == (
            "511 Alberta, Highway 6, 1 event: General Information on HWY-6 Both Directions Near "
            "Alberta Canada Montana United States Border. All lanes closed. Activities: Road "
            "Closed Comments: Chief Mountain Border Crossing located on Highway 6 near Waterton "
            "Lakes National Park - CLOSED for the season until May 15, 2027."
        )


def test_roads_on_a_quiet_highway_in_another_province() -> None:
    with fake_web(ALBERTA):
        reply = road("hwy 2 alberta")
    assert reply == "No 511 Alberta closures or incidents on Highway 2 right now."


def test_an_unmapped_event_matches_by_its_place_name() -> None:
    unmapped = ALBERTA | {"https://511.alberta.ca/map/mapIcons/Incidents": {"item2": []}}
    sheep_creek = place("Sheep Creek", 49.0, -100.0, "Alberta")
    with fake_web(unmapped | {OPEN_METEO_GEOCODE: sheep_creek}):
        reply = road("Sheep Creek")
    assert reply is not None
    assert reply.startswith("511 Alberta, near Sheep Creek, 1 event: ")
    assert "HWY-40" in reply


QUEBEC = {"https://ws.mapserver.transports.gouv.qc.ca/": load("quebec_evenements.json")}
BEAUCE = "Fermeture de 1 voie sur 2: A-73 à la hauteur route du Golf (Beauceville)"


def test_roads_near_a_quebec_town() -> None:
    beauceville = place("Beauceville", 46.21, -70.78, "Quebec")
    with fake_web(QUEBEC | {OPEN_METEO_GEOCODE: beauceville}):
        assert road("Beauceville") == f"Québec 511, near Beauceville, 1 event: {BEAUCE}"


@pytest.mark.parametrize("text", ["73 qc", "A-73 in Quebec", "a73 québec"])
def test_roads_on_a_quebec_highway(text: str) -> None:
    with fake_web(QUEBEC) as calls:
        assert road(text) == f"Québec 511, Highway 73, 1 event: {BEAUCE}"
    assert calls[0][1]["srsName"] == "EPSG:4326"


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
