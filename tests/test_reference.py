from conftest import DDG, WIKI_SEARCH, WIKI_SUMMARY, fake_web, load

from remotesearch.commands.reference import (
    WIKTIONARY,
    define,
    duckduckgo,
    stackoverflow,
    wikipedia,
)


def test_wikipedia_reads_the_recorded_summary() -> None:
    responses = {
        WIKI_SEARCH: load("wikipedia_search.json"),
        WIKI_SUMMARY: load("wikipedia_summary.json"),
    }
    with fake_web(responses) as calls:
        result = wikipedia("photosynthesis")
    assert result is not None
    assert result.startswith("Photosynthesis is a system of biological processes by which")
    assert calls[1][0] == WIKI_SUMMARY + "Photosynthesis"


def test_wikipedia_escapes_a_slash_in_the_title() -> None:
    responses = {
        WIKI_SEARCH: {"query": {"search": [{"title": "AC/DC"}]}},
        WIKI_SUMMARY: {"type": "standard", "extract": "AC/DC are an Australian rock band."},
    }
    with fake_web(responses) as calls:
        assert wikipedia("AC/DC") == "AC/DC are an Australian rock band."
    assert calls[1][0] == WIKI_SUMMARY + "AC%2FDC"


def test_wikipedia_prefixes_the_article_title() -> None:
    responses = {
        WIKI_SEARCH: {"query": {"search": [{"title": "Purified water"}]}},
        WIKI_SUMMARY: {"type": "standard", "extract": "Water that has been filtered.[1]"},
    }
    with fake_web(responses) as calls:
        result = wikipedia("purify water")
    assert result == "Purified water: Water that has been filtered."
    assert calls[1][0] == WIKI_SUMMARY + "Purified_water"


def test_wikipedia_skips_disambiguation_pages_and_empty_searches() -> None:
    responses = {
        WIKI_SEARCH: {"query": {"search": [{"title": "Mercury"}]}},
        WIKI_SUMMARY: {"type": "disambiguation", "extract": "Mercury may refer to:"},
    }
    with fake_web(responses):
        assert wikipedia("mercury") is None
    with fake_web({WIKI_SEARCH: {"query": {"search": []}}}):
        assert wikipedia("zzqx") is None


def test_duckduckgo_reads_the_recorded_abstract() -> None:
    with fake_web({DDG: load("ddg_instant_albedo.json")}):
        result = duckduckgo("albedo")
    assert result is not None
    assert result.startswith("Albedo is the fraction of sunlight that is diffusely reflected")


def test_duckduckgo_related_topic_needs_a_matching_title() -> None:
    topics = {
        "AbstractText": "",
        "RelatedTopics": [
            {"FirstURL": "https://duckduckgo.com/Hiking_boot", "Text": "Hiking boot, for blisters"},
            {"FirstURL": "https://duckduckgo.com/Blister", "Text": "Blister A pocket of fluid."},
        ],
    }
    with fake_web({DDG: topics}):
        assert duckduckgo("treat a blister") == "Blister A pocket of fluid."
        assert duckduckgo("best tent stakes") is None


def test_duckduckgo_prefers_a_direct_answer() -> None:
    with fake_web({DDG: {"Answer": "", "AbstractText": " Albedo is reflectance. "}}):
        assert duckduckgo("albedo") == "Albedo is reflectance."
    with fake_web({DDG: ["not", "a", "dict"]}):
        assert duckduckgo("albedo") is None


def test_dictionary_reads_the_recorded_entry() -> None:
    with fake_web({WIKTIONARY: load("wiktionary_portage.json")}) as calls:
        assert define("portage") == (
            "(noun) An act of carrying, especially the carrying of a boat overland between two "
            "waterways. (verb) To carry a boat overland."
        )
    assert [url for url, _ in calls] == [WIKTIONARY + "portage"]


def test_dictionary_takes_two_senses_and_skips_empty_ones() -> None:
    entry = {
        "en": [
            {
                "partOfSpeech": "Noun",
                "definitions": [{"definition": "<span></span>"}, {"definition": "Reflectivity."}],
            },
            {"partOfSpeech": "Verb", "definitions": [{"definition": "To reflect."}]},
            {"partOfSpeech": "Adjective", "definitions": [{"definition": "Unused."}]},
        ]
    }
    with fake_web({WIKTIONARY: entry}) as calls:
        assert define("half life") == "(noun) Reflectivity. (verb) To reflect."
    assert [url for url, _ in calls] == [WIKTIONARY + "half%20life"]


def test_dictionary_tries_a_capitalized_word_in_lower_case() -> None:
    german = {"de": [{"partOfSpeech": "Noun", "definitions": [{"definition": "albedo"}]}]}
    english = {"en": [{"partOfSpeech": "Noun", "definitions": [{"definition": "Reflectivity."}]}]}

    def respond(url: str, _params: dict) -> dict:
        return german if url.endswith("Albedo") else english

    with fake_web({WIKTIONARY: respond}) as calls:
        assert define("Albedo") == "(noun) Reflectivity."
    assert [url for url, _ in calls] == [WIKTIONARY + "Albedo", WIKTIONARY + "albedo"]
    # Wiktionary answers a word it doesn't have with a 404, which comes through as None.
    with fake_web({}) as calls:
        assert define("zzqx") is None
    assert len(calls) == 1


def test_stackoverflow_reads_the_recorded_answer() -> None:
    responses = {
        "https://api.stackexchange.com/2.3/search/advanced": load("stackexchange_search.json"),
        "https://api.stackexchange.com/2.3/questions/231767/answers": load(
            "stackexchange_answers.json"
        ),
    }
    with fake_web(responses):
        result = stackoverflow("python yield")
    assert result is not None
    assert result.startswith(
        'What does the "yield" keyword do in Python? To understand what yield does, you must '
        "understand what generators are."
    )


def test_stackoverflow_question_with_no_answers() -> None:
    responses = {
        "https://api.stackexchange.com/2.3/search/advanced": {
            "items": [{"title": "Why &amp; how?", "question_id": 7}]
        },
        "https://api.stackexchange.com/2.3/questions/7/answers": {"items": []},
    }
    with fake_web(responses):
        assert stackoverflow("why") == "Why & how? (no answers yet)"
