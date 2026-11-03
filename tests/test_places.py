from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import pytest
from conftest import OPEN_METEO_GEOCODE, fake_web, load

from remotesearch.places import find, geocode, place_label

# Before the recorded alerts expire and before the recorded tides, on a Sunday in Tofino.
NOW = datetime(2026, 9, 27, 17, 0, tzinfo=UTC)

TOFINO = load("open_meteo_geocode_tofino.json")


@pytest.fixture(autouse=True)
def _clock() -> Any:
    with patch("remotesearch.net.now", return_value=NOW):
        yield


def test_place_label() -> None:
    hit = {"name": "Tofino", "admin1": "British Columbia", "country_code": "CA"}
    assert place_label(hit) == "Tofino, British Columbia, CA"
    assert place_label({"name": "Atlantis"}) == "Atlantis"


def test_geocode_is_asked_once_per_name() -> None:
    with fake_web({OPEN_METEO_GEOCODE: TOFINO}) as calls:
        geocode("Tofino")
        geocode("Tofino")
    assert len(calls) == 1


def towns(by_name: dict[str, dict[str, Any]]) -> Any:
    """A geocoder that knows each town by its bare name and nothing else."""
    return lambda url, params: by_name.get(params["name"], {})


BRECKENRIDGE = {
    "Breckenridge": {
        "results": [
            {
                "name": "Breckenridge",
                "admin1": "Texas",
                "country": "United States",
                "country_code": "US",
            },
            {
                "name": "Breckenridge",
                "admin1": "Colorado",
                "country": "United States",
                "country_code": "US",
            },
        ]
    }
}
BRIGHTON = {
    "Brighton": {
        "results": [
            {
                "name": "Brighton",
                "admin1": "Colorado",
                "country": "United States",
                "country_code": "US",
            },
            {
                "name": "Brighton",
                "admin1": "England",
                "country": "United Kingdom",
                "country_code": "GB",
            },
        ]
    }
}


@pytest.mark.parametrize(
    ("text", "known", "admin1"),
    [
        ("Breckenridge CO", BRECKENRIDGE, "Colorado"),
        ("in Breckenridge, colorado", BRECKENRIDGE, "Colorado"),
        ("Brighton UK", BRIGHTON, "England"),
        ("Brighton United Kingdom", BRIGHTON, "England"),
        ("Brighton England", BRIGHTON, "England"),
    ],
)
def test_a_trailing_state_or_country_picks_the_right_town(
    text: str, known: dict[str, Any], admin1: str
) -> None:
    with fake_web({OPEN_METEO_GEOCODE: towns(known)}):
        hit = find(text)
    assert hit is not None
    assert hit["admin1"] == admin1


def test_a_region_none_of_the_towns_are_in_finds_nothing() -> None:
    with fake_web({OPEN_METEO_GEOCODE: towns(BRECKENRIDGE)}):
        assert find("Breckenridge NY") is None


def test_a_town_comes_before_a_peak_or_pass_the_geocoder_ranks_first() -> None:
    known = {
        "Tofino": {
            "results": [
                {"name": "Tofino", "admin1": "Veneto", "feature_code": "PK"},
                {"name": "Tofino", "admin1": "British Columbia", "feature_code": "PPL"},
            ]
        },
        "Rogers Pass": {
            "results": [
                {"name": "Rogers Pass", "admin1": "KwaZulu-Natal", "feature_code": "PASS"},
                {"name": "Rogers Pass", "admin1": "British Columbia", "feature_code": "PPLL"},
            ]
        },
    }
    with fake_web({OPEN_METEO_GEOCODE: towns(known)}):
        assert [(find(t) or {}).get("admin1") for t in known] == ["British Columbia"] * 2


def test_the_town_and_its_state_beat_a_longer_name_starting_the_same() -> None:
    known: dict[str, Any] = {
        "Portland ME": {"results": [{"name": "Portland Meadows", "admin1": "Oregon"}]},
        "Portland": {
            "results": [
                {"name": "Portland", "admin1": "Oregon", "feature_code": "PPLA2"},
                {"name": "Portland", "admin1": "Maine", "feature_code": "PPLA2"},
            ]
        },
    }
    with fake_web({OPEN_METEO_GEOCODE: towns(known)}):
        hit = find("Portland ME")
    assert hit is not None
    assert hit["admin1"] == "Maine"
