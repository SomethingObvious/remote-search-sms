from remotesearch.text import content_words, looks_relevant


def test_content_words_drops_stopwords() -> None:
    assert content_words("why is the sky blue") == {"sky", "blue"}
    assert content_words("How do I treat a BLISTER") == {"treat", "blister"}


def test_looks_relevant_rejects_the_real_misfires() -> None:
    """These are the actual top Wikipedia hits for these texts.

    Wikipedia search returns something for any query built from real words, so without
    this check a first-aid question got a travel memoir and a road question got a
    highway in another province.
    """
    assert looks_relevant("why is the sky blue", "Diffuse sky radiation") is True
    assert looks_relevant("how long to boil water to purify it", "Purified water") is True
    assert looks_relevant("photosynthesis", "Photosynthesis") is True

    assert looks_relevant("how do I treat a blister on a hike", "The Salt Path") is False
    assert looks_relevant("what time is sunset in Tofino", "British Columbia") is False
    assert looks_relevant("best hiking boots", "Vibram S.p.A.") is False


def test_looks_relevant_stem_matching() -> None:
    assert looks_relevant("purify water", "Purified water") is True  # shared 5-letter prefix
    assert looks_relevant("open the gate", "Opera house") is False  # 3 letters isn't enough
    assert looks_relevant("montreal forets", "Forêts de Montréal") is True  # accents drop out


def test_looks_relevant_matches_short_word_queries() -> None:
    assert looks_relevant("AC/DC", "AC/DC") is True
    assert looks_relevant("UV", "Ultraviolet") is False
