from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import pytest
from conftest import OPEN_METEO_GEOCODE, fake_web, load

from remotesearch.places import geocode, place_label

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
