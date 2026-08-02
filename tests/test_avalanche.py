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
