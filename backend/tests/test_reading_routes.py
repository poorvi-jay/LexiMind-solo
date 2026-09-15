"""The four /reading routes (M4 phase 4).

Assertions are on shape and on the properties the Reading page relies on, not
on exact model output — three of these four routes involve a language model, a
dictionary API or a pronunciation dictionary, and pinning their exact words
would produce a suite that fails when nothing is wrong.

/reading/simplify is the awkward one: it needs GROQ_API_KEY. CI deliberately
does not carry one (a paid call on every push, to cover a route the phase-9
smoke test exercises against the deployed app anyway), so there it must 503.
A developer with a key in backend/.env gets a 200. Both are correct, and the
test accepts either while checking the shape of whichever it got.
"""

from __future__ import annotations

import pytest

PASSAGE = (
    "The mitochondrion is the powerhouse of the cell. It generates most of the "
    "cell's supply of adenosine triphosphate, which is subsequently used "
    "throughout the cell as a source of chemical energy."
)


@pytest.fixture
def reader(api, new_user):
    _, headers = new_user("reading")

    def post(path: str, body: dict):
        return api.post(path, headers=headers, json=body)

    return post


# ── complexity (F43) ───────────────────────────────────────────────────

def test_complexity_returns_the_full_indicator(reader):
    response = reader("/reading/complexity", {"text": PASSAGE})
    assert response.status_code == 200

    body = response.json()
    for field in (
        "flesch_kincaid_grade",
        "level_label",
        "hard_word_pct",
        "word_count",
        "sentence_count",
        "est_reading_time_s",
    ):
        assert field in body, f"/reading/complexity dropped {field}"

    assert body["level_label"] in ("Easy", "Moderate", "Hard", "Very Hard")
    assert body["sentence_count"] == 2
    assert 0 <= body["hard_word_pct"] <= 100


def test_empty_text_is_rejected_rather_than_scored(reader):
    """400, not a zeroed score.

    simplification_service.flesch_kincaid_grade guards its own division and
    returns 0.0 for empty input, so a 200 with zeroes would also be defensible.
    The routes choose to reject instead — a reader who has selected nothing
    gets told so, rather than being shown a confident "Grade 0.0 - Easy".
    Both /reading/complexity and /reading/simplify do this.
    """
    for path in ("/reading/complexity", "/reading/simplify"):
        response = reader(path, {"text": "   "})
        assert response.status_code == 400, f"{path} scored whitespace"
        assert response.json()["detail"] == "No text provided."


def test_harder_prose_scores_higher_than_simple_prose(reader):
    """A relative assertion, which is stable where an absolute one is not."""
    simple = reader("/reading/complexity", {"text": "The cat sat. The dog ran."})
    hard = reader("/reading/complexity", {"text": PASSAGE})

    assert (
        hard.json()["flesch_kincaid_grade"]
        > simple.json()["flesch_kincaid_grade"]
    )


# ── syllabify (M3, pyphen) ─────────────────────────────────────────────

def test_syllabify_returns_parts_per_word(reader):
    response = reader(
        "/reading/syllabify", {"words": ["photosynthesis", "cat", "water"]}
    )
    assert response.status_code == 200

    results = response.json()["results"]
    assert results["cat"] == ["cat"], "a one-syllable word is one part"
    assert len(results["photosynthesis"]) > 1
    assert "".join(results["photosynthesis"]) == "photosynthesis", (
        "the parts must reassemble into the original word - they are joined "
        "with a separator in the UI, not used to respell it"
    )


def test_syllabify_shares_the_batch_ceiling_with_classify(reader):
    assert reader("/reading/syllabify", {"words": ["cat"] * 5000}).status_code == 200
    assert reader("/reading/syllabify", {"words": ["cat"] * 5001}).status_code == 422


def test_syllabify_of_nothing_is_an_empty_result(reader):
    response = reader("/reading/syllabify", {"words": []})
    assert response.status_code == 200
    assert response.json()["results"] == {}


# ── define (F44 / AC-34) ───────────────────────────────────────────────

def test_define_returns_a_definition_and_syllables(reader):
    """Either source is acceptable — dictionaryapi.dev, or the WordNet fallback.

    The fallback exists because the network is not guaranteed, and it costs
    the phonetic spelling. Asserting on `phonetic` would therefore make this
    test fail exactly when the fallback is doing its job.
    """
    response = reader("/reading/define", {"word": "photosynthesis"})
    assert response.status_code == 200

    body = response.json()
    assert body["word"] == "photosynthesis"
    assert body["definition"], "no definition from either source"
    assert body["syllable_count"] > 1
    assert body["syllable_parts"]
    assert body["source"] in ("dictionaryapi", "wordnet"), body["source"]


def test_define_includes_all_meanings(reader):
    """AC-34 wants every sense, not just the first."""
    body = reader("/reading/define", {"word": "run"}).json()
    assert isinstance(body["meanings"], list)
    assert body["meanings"], "no meanings for a word with many"
    assert "definitions" in body["meanings"][0]


def test_syllable_count_and_parts_may_disagree(reader):
    """A documented limitation, pinned so it stays deliberate.

    The count comes from the CMU dictionary and the pieces from pyphen, and
    they use different rules — "mitochondria" is 5 by CMU and 4 pieces by
    pyphen. This asserts only that both are present and positive, so nobody
    later "fixes" the disagreement by deriving one from the other and quietly
    changes what the count means.
    """
    body = reader("/reading/define", {"word": "mitochondria"}).json()
    assert body["syllable_count"] > 0
    assert len(body["syllable_parts"]) > 0


# ── simplify (F42, Groq) ───────────────────────────────────────────────

def test_simplify_either_works_or_degrades_cleanly(reader):
    """200 with a rewrite, or 503 with a message the reader can act on.

    Note what a 503 does and does not prove. simplification_service catches
    every exception from the Groq call and returns 503, so a missing key, a
    network failure and a retired model ID are indistinguishable here. The
    contract being tested is "this route degrades instead of crashing the
    page", not "the key is absent".
    """
    response = reader("/reading/simplify", {"text": PASSAGE})
    assert response.status_code in (200, 503), response.text[:300]

    if response.status_code == 503:
        assert "detail" in response.json()
        return

    body = response.json()
    assert body["simplified_text"].strip(), "a 200 with nothing in it"
    assert body["reading_level"] in ("Easy", "Moderate", "Hard", "Very Hard")
    assert isinstance(body["changes_made"], bool)
    assert 0 <= body["simplified_hard_word_pct"] <= 100
