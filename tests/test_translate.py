import pytest
from conftest import fake_web, load

from remotesearch.commands.translate import mymemory, translation_request
from remotesearch.net import SourceError


def test_translation_request() -> None:
    assert translation_request("where is the bus to french") == ("where is the bus", "French", "fr")
    assert translation_request("go to the store into Spanish.") == (
        "go to the store",
        "Spanish",
        "es",
    )
    assert translation_request("hello to klingon") is None
    assert translation_request("hello") is None


def test_mymemory_reads_the_recorded_translation_and_its_errors() -> None:
    url = "https://api.mymemory.translated.net/"
    with fake_web({url: load("mymemory_de.json")}) as calls:
        assert mymemory("Where is the train station", "de") == "Wo ist der Bahnhof"
    assert calls[0][1] == {"q": "Where is the train station", "langpair": "autodetect|de"}
    spent = load("mymemory_de.json") | {"quotaFinished": True}
    with fake_web({url: spent}), pytest.raises(SourceError, match="out of free translations"):
        mymemory("hello", "de")
    bad = {"responseStatus": 403, "responseDetails": "'XX' IS AN INVALID TARGET LANGUAGE"}
    with fake_web({url: bad}), pytest.raises(SourceError, match="INVALID TARGET LANGUAGE"):
        mymemory("hello", "xx")
