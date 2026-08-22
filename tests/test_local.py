import re
from unittest.mock import patch

from conftest import OPEN_METEO_GEOCODE, fake_web, load

from remotesearch.commands.local import hours, local_time

NOMINATIM = "https://nominatim.openstreetmap.org/"


def test_time_in_a_place() -> None:
    with fake_web({OPEN_METEO_GEOCODE: load("open_meteo_geocode_tofino.json")}):
        reply = local_time("in Tofino")
    assert reply is not None
    assert re.fullmatch(
        r"Tofino, British Columbia, CA: \d\d:\d\d \w{3} \w{3} \d{1,2} \(([A-Z]{3,4})\)", reply
    ), reply
    with fake_web({}):
        assert local_time("Zzqx") is None


def test_business_reads_the_recorded_place() -> None:
    with fake_web({NOMINATIM: load("nominatim_tim_hortons_hope.json")}) as calls:
        assert hours("Tim Hortons in Hope BC") == (
            "Tim Hortons, 250 Old Hope-Princeton Way, Hope. Open Mo-Su 05:00-22:00. +1-604-860-0601"
        )
        hours("Tim Hortons in Hope BC")
    assert calls == [
        (
            "https://nominatim.openstreetmap.org/search",
            {
                "q": "Tim Hortons, Hope BC",
                "format": "jsonv2",
                "extratags": 1,
                "addressdetails": 1,
                "limit": 3,
            },
        )
    ]  # asked once, as Nominatim's policy wants results kept


def test_business_skips_the_wrong_place() -> None:
    wrong = [{"name": "Hope Station House", "extratags": {"opening_hours": "24/7"}, "address": {}}]
    with fake_web({NOMINATIM: wrong}):
        assert hours("Tim Hortons") is None
    bare = [{"name": "Tim Hortons", "address": {"road": "Main St", "town": "Hope"}}]
    with fake_web({NOMINATIM: bare}):
        assert hours("Tim Hortons Hope") == "Tim Hortons, Main St, Hope. No hours listed"


def test_business_waits_a_second_between_nominatim_calls() -> None:
    with patch("remotesearch.net.session") as session, patch("time.sleep") as sleep:
        session.return_value.get.return_value.status_code = 200
        session.return_value.get.return_value.json.return_value = []
        hours("first place")
        hours("second place")
    waits = [c.args[0] for c in sleep.call_args_list]
    assert len(waits) == 1
    assert 0.9 < waits[0] <= 1.0


def test_business_skips_the_town_itself() -> None:
    found = [
        {"name": "Squamish", "category": "boundary", "address": {}},
        {
            "name": "Save-On-Foods",
            "category": "shop",
            "extratags": {"opening_hours": "Mo-Su 09:00-21:00"},
            "address": {"road": "Third Avenue", "town": "Squamish"},
        },
    ]
    with fake_web({NOMINATIM: found}):
        assert hours("Save-On-Foods Squamish") == (
            "Save-On-Foods, Third Avenue, Squamish. Open Mo-Su 09:00-21:00"
        )
