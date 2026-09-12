"""
F33/F34 — feature extraction and the trained word-difficulty classifier.

No server needed: this loads backend/ml/classifier.joblib directly. It covers
AC-27's latency budget and the behaviour the amended AC-26 rests on, since
accuracy alone turned out to be a poor gate (see backend/ml/README.md).
"""

import time

import pytest

from backend.services import classifier_service as cs

SAMPLES = {
    "children's story": (
        "The little cat sat on the mat and looked at the big red ball. She was "
        "very happy to play with her friend in the garden after school."
    ),
    "academic": (
        "The encyclopedia entry describes semiconductor fabrication as a "
        "photolithographic process requiring extraordinarily precise "
        "environmental controls and specialised equipment."
    ),
}


@pytest.fixture(scope="module", autouse=True)
def model_loaded():
    """The artifact is committed, so a failure here is a real problem."""
    cs.warm_up()
    assert cs.readiness() == "ready", (
        "classifier.joblib did not load; run backend/ml/train_classifier.py"
    )


# ── normalisation ──────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "raw, expected",
    [
        ("encyclopedia,", "encyclopedia"),
        ('"The', "the"),
        ("cat.", "cat"),
        ("don't", "don't"),        # internal apostrophe survives
        ("well-known", "well-known"),  # internal hyphen survives
        ("...", ""),
        ("   Word  ", "word"),
    ],
)
def test_normalize_word_strips_only_edge_punctuation(raw, expected):
    """Internal apostrophes and hyphens must survive.

    wordfreq scores "don't" at 1.58e-3 but "dont" at 5.5e-5, and "well-known"
    at 2.0e-4 but "wellknown" at 3.7e-8 — flattening them would make ordinary
    words look rare, which is to say hard.
    """
    assert cs.normalize_word(raw) == expected


def test_features_are_syllables_frequency_length_in_that_order():
    syllables, frequency, length = cs.extract_features("encyclopedia")
    assert (syllables, length) == (5.0, 12.0)
    assert frequency == pytest.approx(3.31e-06, rel=0.1)


def test_features_ignore_trailing_punctuation():
    """hard_word_pct() splits raw text, so tokens arrive with punctuation attached."""
    assert cs.extract_features("encyclopedia,") == cs.extract_features("encyclopedia")


def test_unreadable_token_yields_zeroed_features():
    assert cs.extract_features("...") == [0.0, 0.0, 0.0]


# ── labels ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "word, expected",
    [("encyclopedia", "Hard"), ("semiconductor", "Hard"), ("photolithographic", "Hard"),
     ("cat", "Easy"), ("the", "Easy")],
)
def test_sanity_words_are_labelled_as_expected(word, expected):
    """The placeholder mislabelled the first three; the build guide names them."""
    assert cs.classify_word_label(word) == expected


def test_difficult_is_no_longer_easy():
    """'difficult' was the placeholder's most embarrassing miss."""
    assert cs.classify_word_label("difficult") in {"Medium", "Hard"}


def test_batch_preserves_order_and_handles_repeats():
    words = ["the", "encyclopedia", "the", "cat", "encyclopedia"]
    labels = [label for label, _ in cs.classify_batch(words)]
    assert labels == ["Easy", "Hard", "Easy", "Easy", "Hard"]


def test_empty_batch_is_empty():
    assert cs.classify_batch([]) == []


def test_confidence_is_a_probability():
    for _, confidence in cs.classify_batch(["encyclopedia", "cat", "the"]):
        assert 0.0 <= confidence <= 1.0


# ── what a reader actually sees ────────────────────────────────────────
def test_hard_word_share_follows_the_difficulty_of_the_prose():
    """Type-level class balance misleads; running text is what matters.

    65% of the training vocabulary is labelled Hard, yet ordinary prose is
    mostly common words — so only a sensible fraction is ever highlighted.
    """
    easy = cs.hard_word_pct(SAMPLES["children's story"])
    academic = cs.hard_word_pct(SAMPLES["academic"])
    assert easy == 0.0
    assert academic > 20.0
    assert academic > easy


def test_the_retired_heuristic_really_was_miscalibrated():
    """Kept as a fallback, and as the record of why it was replaced."""
    assert cs._heuristic_label("encyclopedia") == "Medium"   # should be Hard
    assert cs.hard_word_pct("the quick encyclopedia semiconductor") == 50.0


# ── AC-27 ──────────────────────────────────────────────────────────────
def test_two_hundred_words_classify_within_the_budget():
    """AC-27: under 500ms for 200 words. Measured around 210ms.

    This is the service-level cost — feature extraction plus inference — with
    no HTTP in the way; the endpoint adds its own overhead but also
    de-duplicates, which real prose benefits from heavily.
    """
    words = ["encyclopedia", "cat", "photolithographic", "the", "running"] * 40
    assert len(words) == 200

    start = time.perf_counter()
    cs.classify_batch(words)
    elapsed = time.perf_counter() - start

    assert elapsed < 0.5, f"200 words took {elapsed:.3f}s"
