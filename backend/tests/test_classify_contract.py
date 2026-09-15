"""The /classify response contract (M4 phase 4).

test_classifier.py already covers the classifier *service* — normalize_word,
feature order, label quality, the AC-27 latency budget. This file covers the
HTTP endpoint, and specifically the one property the Reading page depends on
and nothing previously asserted.

routers/classify.py states it in prose:

    `word` echoes the caller's ORIGINAL token, punctuation and all.
    ReadingPage matches on it ... Echoing a cleaned word would silently
    break highlighting for hyphenated words like "well-known".

"Silently" is the operative word. The classifier would still be right, the
response would still be well-formed, and the labels would still be correct —
highlighting would just stop landing on the words it marked. Nothing would
fail except the feature. So the echo is pinned here.
"""

from __future__ import annotations

import pytest

# Tokens whose cleaned form differs from their raw form. normalize_word strips
# non-letters from a token's EDGES, so each of these would come back changed
# if the endpoint echoed the normalized token instead of the original.
AWKWARD_TOKENS = [
    "well-known",     # inner hyphen: cleaning keeps it, but the edges matter
    "cat,",           # trailing comma
    '"quoted"',       # both edges
    "(parenthesised)",
    "don't",          # inner apostrophe
    "end.",
    "—dashed",        # leading em dash
    "19th",           # digits are stripped from the edges; "th" survives
]


@pytest.fixture
def classify(api, new_user):
    _, headers = new_user("classify")

    def call(words: list[str]):
        return api.post("/classify", headers=headers, json={"words": words})

    return call


def test_every_token_is_echoed_exactly(classify):
    """The contract the Reading page indexes on."""
    response = classify(AWKWARD_TOKENS)
    assert response.status_code == 200

    echoed = [result["word"] for result in response.json()["results"]]
    assert echoed == AWKWARD_TOKENS, (
        "the endpoint returned cleaned tokens instead of the originals. "
        "ReadingPage matches highlighting on this field, so it would stop "
        "landing on the right words while every label stayed correct."
    )


def test_results_line_up_with_the_request(classify):
    """Same length, same order — the client zips these by position."""
    words = ["the", "encyclopedia", "cat", "semiconductor"]
    response = classify(words)

    results = response.json()["results"]
    assert len(results) == len(words)
    assert [r["word"] for r in results] == words


def test_repeats_are_not_collapsed(classify):
    """A word appearing twice must come back twice.

    De-duplicating would be a reasonable-looking optimisation and would break
    the positional zip above.
    """
    response = classify(["cat", "cat", "cat"])
    assert len(response.json()["results"]) == 3


def test_labels_and_confidences_are_in_range(classify):
    response = classify(["cat", "encyclopedia", "difficult", "the"])

    for result in response.json()["results"]:
        assert result["label"] in ("Easy", "Medium", "Hard"), (
            f"unexpected label {result['label']!r} for {result['word']!r}; "
            "the Reading page styles on exactly these three"
        )
        assert 0.0 <= result["confidence"] <= 1.0, (
            f"confidence {result['confidence']} for {result['word']!r} is not "
            "a probability"
        )


def test_an_empty_list_is_an_empty_result_not_an_error(classify):
    """Deliberate: the Reading page can clear its text at any moment.

    classify.py gives `words` a default_factory rather than making it required
    so this is a 200 with nothing in it, not a 422 the client has to special-case.
    """
    response = classify([])
    assert response.status_code == 200
    assert response.json()["results"] == []


def test_the_batch_ceiling_is_enforced(classify):
    """5000 is the documented maximum, and the Reading page batches to it.

    AC-28 requires one classify call per document; the page splits unique
    words into batches of 5000 because of this limit. If the limit moved,
    the page would start getting 422s mid-document.
    """
    assert classify(["word"] * 5000).status_code == 200
    assert classify(["word"] * 5001).status_code == 422
