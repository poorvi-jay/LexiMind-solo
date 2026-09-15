"""The three /nlp routes (M4 phase 4).

These run on spaCy, LanguageTool and DistilGPT-2, so the assertions are about
structure and about the availability flags — not about which suggestions come
back. A test that pinned exact suggestions would fail on a model update while
the feature worked fine.

The availability flags matter more than they look. /nlp/check answers 200 even
when LanguageTool is missing, reporting grammar_available: false and returning
spelling and homophone results only. That is a deliberate graceful degradation
(F27 needs Java, which not every environment has) and it means a test asserting
only "200" would pass against a backend doing two thirds of the job.
"""

from __future__ import annotations

import pytest

# Deliberately wrong in three different ways: "libary" and "tomorow" are
# misspellings, "Their" should be "They're", "too" should be "to".
MESSY = "Their going too the libary tomorow"


@pytest.fixture
def writer(api, new_user):
    _, headers = new_user("nlp")

    def post(path: str, body: dict):
        return api.post(path, headers=headers, json=body)

    return post


@pytest.fixture(scope="session")
def grammar_ready(api) -> bool:
    return api.get("/health").json()["models"]["grammar"] == "ready"


# ── /nlp/check (F26 - F28) ─────────────────────────────────────────────

def test_check_returns_issues_with_offsets_into_the_submitted_text(writer):
    """The editor highlights by character offset, so the offsets must be usable.

    This is the assertion that would catch an off-by-one or a mismatch
    introduced by normalising the text before checking it: `text` has to be
    exactly what sits between start and end in what the caller sent.
    """
    response = writer("/nlp/check", {"text": MESSY})
    assert response.status_code == 200

    body = response.json()
    assert body["issues"], "no issues found in deliberately broken text"

    for issue in body["issues"]:
        assert issue["type"] in ("spelling", "grammar", "homophone")
        assert 0 <= issue["start"] < issue["end"] <= len(MESSY)
        assert MESSY[issue["start"]:issue["end"]] == issue["text"], (
            f"offsets {issue['start']}:{issue['end']} point at "
            f"{MESSY[issue['start']:issue['end']]!r}, but the issue says "
            f"{issue['text']!r} - the editor would highlight the wrong span"
        )
        assert issue["message"]
        assert isinstance(issue["suggestions"], list)


def test_check_finds_the_misspellings(writer):
    """Spelling runs off wordfreq and needs no Java, so this always applies."""
    body = writer("/nlp/check", {"text": MESSY}).json()
    flagged = {i["text"].lower() for i in body["issues"] if i["type"] == "spelling"}

    assert "libary" in flagged, f"missed an obvious misspelling; got {flagged}"


def test_check_counts_agree_with_the_issue_list(writer):
    """`counts` is what the UI badges show; the list is what it highlights.

    They are produced separately, so they can drift.
    """
    body = writer("/nlp/check", {"text": MESSY}).json()

    from collections import Counter

    actual = Counter(issue["type"] for issue in body["issues"])
    for issue_type, count in body["counts"].items():
        assert count == actual.get(issue_type, 0), (
            f"counts says {count} {issue_type} issues, the list has "
            f"{actual.get(issue_type, 0)}"
        )


def test_check_reports_whether_grammar_is_available(writer, grammar_ready):
    """The flag must tell the truth, because the UI shows a note based on it."""
    body = writer("/nlp/check", {"text": MESSY}).json()

    assert body["grammar_available"] is grammar_ready, (
        "grammar_available disagrees with /health's view of LanguageTool"
    )
    if not body["grammar_available"]:
        assert body["grammar_unavailable_reason"], (
            "unavailable without a reason leaves the UI nothing to display"
        )


def test_check_of_clean_text_finds_nothing(writer):
    body = writer("/nlp/check", {"text": "The cat sat on the mat."}).json()
    assert body["issues"] == []


def test_check_enforces_a_length_ceiling(writer):
    """MAX_CHECK_CHARS exists so one paste cannot occupy the JVM indefinitely."""
    assert writer("/nlp/check", {"text": "word " * 200_000}).status_code == 422


# ── /nlp/predict (F29) ─────────────────────────────────────────────────

def test_predict_returns_at_most_three_words(writer):
    """Three is what the SuggestionBar renders."""
    response = writer("/nlp/predict", {"text": "The cat sat on the"})
    assert response.status_code == 200

    body = response.json()
    assert isinstance(body["words"], list)
    assert len(body["words"]) <= 3
    assert all(isinstance(word, str) and word for word in body["words"])


def test_predict_reports_availability_rather_than_failing(writer):
    """DistilGPT-2 may be absent; the route still answers 200.

    Mid-word completion runs off the prefix vocabulary and keeps working
    without the model, so `available: false` means the word-boundary pills are
    gone — not that the endpoint is broken.
    """
    body = writer("/nlp/predict", {"text": "The cat sat on the"}).json()
    assert isinstance(body["available"], bool)
    if not body["available"]:
        assert body["unavailable_reason"]


def test_predict_on_a_partial_word_completes_it(writer):
    """The mid-word case, which is the prefix vocabulary rather than the model."""
    body = writer("/nlp/predict", {"text": "I went to the lib"}).json()
    if body["words"]:
        assert all(w.lower().startswith("lib") for w in body["words"]), (
            f"mid-word predictions should extend the prefix; got {body['words']}"
        )


def test_predict_on_empty_text_is_not_an_error(writer):
    response = writer("/nlp/predict", {"text": ""})
    assert response.status_code == 200
    assert isinstance(response.json()["words"], list)


# ── /nlp/predict/phrase (F29) ──────────────────────────────────────────

def test_phrase_returns_a_string_and_a_flag(writer):
    """`phrase` is "" when there isn't a good continuation - not null."""
    response = writer("/nlp/predict/phrase", {"text": "The cat sat on the"})
    assert response.status_code == 200

    body = response.json()
    assert isinstance(body["phrase"], str)
    assert isinstance(body["available"], bool)
    if not body["available"]:
        assert body["unavailable_reason"]
