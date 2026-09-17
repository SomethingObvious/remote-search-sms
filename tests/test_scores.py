import re

import pytest
from conftest import fake_web, load

from remotesearch.commands.scores import game_line, score
from remotesearch.net import SourceError

ESPN = "https://site.api.espn.com/"


def boards(url: str, params: dict) -> dict:
    """Today's NHL board and yesterday's MLB one, recorded, with nothing on the rest."""
    if "hockey/nhl" in url and not params:
        return load("espn_nhl_scoreboard.json")
    if "baseball/mlb" in url and params.get("dates"):
        return load("espn_mlb_yesterday.json")
    return {"events": []}


def test_scores_by_team_name() -> None:
    with fake_web({ESPN: boards}) as calls:
        assert score("Canucks") == "VAN @ EDM, 9/29 - 10:00 PM EDT"
        assert score("blue jays") == "CIN 5 @ TOR 1, Final"
    # Seven leagues, today and yesterday, per question.
    assert len(calls) == 28
    assert calls[0] == ("https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard", {})
    assert re.fullmatch(r"\d{8}", calls[1][1]["dates"])


def test_scores_put_finals_before_games_to_come() -> None:
    with fake_web({ESPN: boards}):
        assert score("tor") == "CIN 5 @ TOR 1, Final / MTL @ TOR, 9/29 - 7:00 PM EDT"
        assert score("to") is None  # too short to match inside a name


def test_scores_when_espn_is_down() -> None:
    with fake_web({ESPN: SourceError("ESPN", "it timed out")}), pytest.raises(SourceError):
        score("canucks")
    with fake_web({ESPN: {"events": []}}):
        assert score("canucks") is None


def test_game_line_while_a_game_is_on() -> None:
    event = load("espn_nhl_scoreboard.json")["events"][1]
    event["status"]["type"] |= {"state": "in", "shortDetail": "2nd 10:32"}
    sides = event["competitions"][0]["competitors"]
    sides[0]["score"], sides[1]["score"] = "1", "2"
    assert game_line(event) == "VAN 2 @ EDM 1, 2nd 10:32"
