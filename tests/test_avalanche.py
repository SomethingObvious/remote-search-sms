from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import pytest
from conftest import OPEN_METEO_GEOCODE, fake_web, load

from remotesearch.commands.avalanche import avalanche

AVALANCHE = "https://api.avalanche.ca/"
# Before the recorded alerts expire and before the recorded tides, on a Sunday in Tofino.
NOW = datetime(2026, 9, 27, 17, 0, tzinfo=UTC)

TOFINO = load("open_meteo_geocode_tofino.json")


@pytest.fixture(autouse=True)
def _clock() -> Any:
    with patch("remotesearch.net.now", return_value=NOW):
        yield


def test_avalanche_in_season() -> None:
    with fake_web({OPEN_METEO_GEOCODE: TOFINO, AVALANCHE: load("made_avalanche_winter.json")}):
        assert avalanche("Tofino") == (
            "Avalanche Canada, Sea To Sky, Friday: alpine 3 Considerable, treeline 2 Moderate, "
            "below treeline 1 Low. Problems: Wind slab, Persistent slab. Wind slabs are building "
            "on lee slopes near ridgetops. Be careful around steep, convex terrain."
        )


def test_avalanche_out_of_season_and_out_of_range() -> None:
    with fake_web({OPEN_METEO_GEOCODE: TOFINO, AVALANCHE: load("avalanche_offseason.json")}):
        assert avalanche("Tofino") == (
            "Avalanche Canada: Regular avalanche forecasts have ended for the season and will "
            "resume in November. Avalanche danger may still exist in some high elevation areas."
        )
    with fake_web({OPEN_METEO_GEOCODE: TOFINO, AVALANCHE: load("avalanche_outside.json")}):
        assert avalanche("Tofino") == (
            "Avalanche Canada has no forecast covering Tofino, British Columbia, CA."
        )


AVALANCHE_ORG = "https://api.avalanche.org/"


def us_place(name: str, lat: float, lon: float, admin1: str) -> dict[str, Any]:
    return {
        "results": [
            {
                "name": name,
                "latitude": lat,
                "longitude": lon,
                "admin1": admin1,
                "country_code": "US",
            }
        ]
    }


def test_a_us_zone_gets_its_ratings_by_elevation() -> None:
    responses = {
        OPEN_METEO_GEOCODE: us_place("Crested Butte", 38.89, -107.02, "Colorado"),
        "https://api.avalanche.org/v2/public/products/map-layer": load(
            "avalanche_org_map_layer.json"
        ),
        "https://api.avalanche.org/v2/public/product": load("avalanche_org_caic_2752.json"),
    }
    with fake_web(responses) as calls:
        reply = avalanche("Crested Butte")
    assert reply is not None
    assert reply.startswith(
        "Colorado Avalanche Information Center, Elk Mountains: above treeline 3 Considerable, "
        "near treeline 2 Moderate, below treeline 1 Low. Problems: Persistent Slab, Wind Slab. "
        "Snowfall increases significantly as you climb"
    )
    assert calls[2][1] == {"type": "forecast", "center_id": "CAIC", "zone_id": 2752}


def test_unrated_and_closed_zones_say_so() -> None:
    layer = load("avalanche_org_map_layer.json")
    snoqualmie = us_place("Snoqualmie Pass", 47.365, -121.372, "Washington")
    with fake_web({OPEN_METEO_GEOCODE: snoqualmie, AVALANCHE_ORG + "v2/public/products": layer}):
        assert avalanche("Snoqualmie Pass") == (
            "Northwest Avalanche Center, Snoqualmie Pass: no rating today"
        )
    adirondacks = us_place("Lake Placid", 44.229, -73.798, "New York")
    with fake_web({OPEN_METEO_GEOCODE: adirondacks, AVALANCHE_ORG: layer}) as calls:
        reply = avalanche("Lake Placid")
    assert reply is not None
    assert reply.endswith("Adirondack Park is out of season, so there's no forecast.")
    assert len(calls) == 2  # no forecast to fetch for a zone that's out of season


def test_places_outside_any_zone_or_country() -> None:
    layer = load("avalanche_org_map_layer.json")
    kansas = us_place("Wichita", 37.69, -97.34, "Kansas")
    with fake_web({OPEN_METEO_GEOCODE: kansas, AVALANCHE_ORG: layer}):
        assert (
            avalanche("Wichita")
            == "avalanche.org has no forecast zone covering Wichita, Kansas, US."
        )
    chamonix = {
        "results": [
            {
                "name": "Chamonix",
                "latitude": 45.92,
                "longitude": 6.87,
                "admin1": "Auvergne-Rhone-Alpes",
                "country_code": "FR",
            }
        ]
    }
    with fake_web({OPEN_METEO_GEOCODE: chamonix}) as calls:
        assert avalanche("Chamonix") == (
            "There's no avalanche forecast here for Chamonix, Auvergne-Rhone-Alpes, FR, as "
            "Avalanche Canada and avalanche.org only cover Canada and the US."
        )
    assert len(calls) == 1
