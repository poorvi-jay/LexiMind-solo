"""
backend/services/classifier_service.py
Shared word-difficulty classification logic (F33).

Both /classify (per-word labels shown live in the Reading page) and
simplification_service (before/after hard-word % for /reading/simplify and
/reading/complexity) need to answer "is this word hard?" — they used to do it
two different ways (wordfreq thresholds vs. a >8-characters heuristic), so the
same text could show two disagreeing hard-word percentages on screen at once.
This module is the single source of truth so every caller agrees on the same
definition of "hard."

The trained model (backend/ml/classifier.joblib, see backend/ml/README.md) has
replaced M1's wordfreq-threshold placeholder. That placeholder survives below as
a fallback, because a fresh clone has no artifact until train_classifier.py is
run and the Reading page must still work — degraded, not broken.

Loading is lazy and locked, matching prediction_service's DistilGPT-2 handling:
a cold joblib.load() costs ~2.9s, so it happens once at startup on the lifespan
warm-up thread, never per request.
"""

from __future__ import annotations

import logging
import re
import threading
from pathlib import Path

import numpy as np
from nltk.tokenize import SyllableTokenizer
from wordfreq import word_frequency

logger = logging.getLogger(__name__)

MODEL_PATH = Path(__file__).resolve().parent.parent / "ml" / "classifier.joblib"

LABELS = ("Easy", "Medium", "Hard")

# ── the pre-M3 placeholder, now only a fallback ─────────────────────
# frequency > 1e-4          -> Easy
# 1e-6 <= frequency <= 1e-4 -> Medium
# frequency < 1e-6          -> Hard
EASY_THRESHOLD = 1e-4
MEDIUM_THRESHOLD = 1e-6

# ── feature extraction ──────────────────────────────────────────────
# Imported by backend/ml/train_classifier.py so training and serving compute
# features from the exact same code. Any drift between the two is the classic
# way a model that scored well offline quietly underperforms in production.

# The SSP tokenizer is corpus-free (unlike routers/reading.py's cmudict-based
# counting), so building it is cheap and needs no nltk download.
_SYLLABLES = SyllableTokenizer()

# Strips punctuation only at the edges of a token. Internal apostrophes and
# hyphens must survive: wordfreq scores "don't" at 1.58e-3 but "dont" at
# 5.5e-5, and "well-known" at 2.0e-4 but "wellknown" at 3.7e-8. Flattening them
# would make ordinary words look rare, i.e. hard.
_EDGE_PUNCTUATION = re.compile(r"^[^a-z]+|[^a-z]+$")


def normalize_word(word: str) -> str:
    """Lowercase and strip edge punctuation. May return ''.

    Note this is deliberately NOT the same rule as ReadingPage.jsx's own
    normalizeWord(), which strips punctuation everywhere. That is fine, and must
    stay fine: the frontend keys its lookup table by normalising the `word`
    field we echo back, so both sides apply *its* rule to the same original
    token. This function exists only to clean input for feature extraction.
    """
    return _EDGE_PUNCTUATION.sub("", word.strip().lower())


def extract_features(word: str) -> list[float]:
    """[syllables, frequency, length] — the PRD's exact feature set and order.

    Cleaning the input is an addition to the PRD's literal function body, not a
    change to the feature set. hard_word_pct() splits raw text on whitespace,
    so this receives tokens like '"The' and 'cat.'. wordfreq normalises
    punctuation internally — which is why the frequency-only placeholder never
    had to care — but len() and the syllable tokenizer do not, so two of the
    three features would be corrupted by a trailing comma.
    """
    clean = normalize_word(word)
    if not clean:
        return [0.0, 0.0, 0.0]

    syllables = len(_SYLLABLES.tokenize(clean))
    frequency = word_frequency(clean, "en")
    length = len(clean)
    return [float(syllables), float(frequency), float(length)]


# ── model loading ───────────────────────────────────────────────────
_load_lock = threading.Lock()
_model = None
_load_error: str | None = None


def _get_model():
    """The trained classifier, or None if it could not be loaded.

    Double-checked locking, same shape as prediction_service._get_model(): the
    fast path never takes the lock, and a recorded error means the attempt is
    over — nothing retries within a process.
    """
    global _model, _load_error

    if _model is not None or _load_error is not None:
        return _model

    with _load_lock:
        if _model is not None or _load_error is not None:
            return _model
        try:
            import joblib

            _model = joblib.load(MODEL_PATH)
            logger.info("Loaded word difficulty classifier from %s", MODEL_PATH)
        except Exception as exc:  # noqa: BLE001 — recorded, never raised
            _load_error = str(exc)
            logger.warning(
                "Word difficulty classifier unavailable (%s); falling back to the "
                "wordfreq heuristic. Run backend/ml/train_classifier.py to build it.",
                exc,
            )

    return _model


def warm_up() -> None:
    """Load the model. Called once at startup, off the event loop."""
    _get_model()


def readiness() -> str:
    """'ready' | 'loading' | 'unavailable'. Never blocks.

    Reads the globals directly rather than taking _load_lock, so /health answers
    immediately even while the model is still loading — which is exactly when it
    gets asked.
    """
    if _model is not None:
        return "ready"
    return "unavailable" if _load_error is not None else "loading"


# ── classification ──────────────────────────────────────────────────
def _heuristic_label(word: str) -> str:
    """M1's wordfreq-band placeholder. Only used when the model is missing.

    Known to be miscalibrated — it labels 'encyclopedia' Medium and reports 0.0%
    hard words in "the quick encyclopedia semiconductor" — but a rough label
    beats no highlighting at all on a clone that has not trained the model yet.
    """
    clean = normalize_word(word)
    if not clean:
        return "Easy"

    freq = word_frequency(clean, "en")
    if freq > EASY_THRESHOLD:
        return "Easy"
    if freq >= MEDIUM_THRESHOLD:
        return "Medium"
    return "Hard"


def _heuristic_confidence(word: str, label: str) -> float:
    freq = word_frequency(normalize_word(word), "en")
    if label == "Easy":
        return round(min(1.0, freq * 1_000), 3)
    if label == "Medium":
        return 0.6
    return 0.85 if freq == 0 else 0.7


def classify_batch(words: list[str]) -> list[tuple[str, float]]:
    """Label every word, returned in the order given, as (label, confidence).

    Each *distinct* word is featurised and inferred once and the results are
    mapped back over the original list. Ordinary prose repeats words heavily, so
    this is most of what keeps AC-27's 500ms/200-word budget comfortable.
    """
    if not words:
        return []

    model = _get_model()
    if model is None:
        return [
            (label, _heuristic_confidence(word, label))
            for word, label in ((w, _heuristic_label(w)) for w in words)
        ]

    unique = list(dict.fromkeys(words))  # de-duplicated, order preserved
    features = np.array([extract_features(word) for word in unique], dtype=float)
    labels = model.predict(features)
    probabilities = model.predict_proba(features).max(axis=1)

    resolved = {
        word: (str(label), round(float(probability), 3))
        for word, label, probability in zip(unique, labels, probabilities)
    }
    return [resolved[word] for word in words]


def classify_word_label(word: str) -> str:
    """Return 'Easy' | 'Medium' | 'Hard' for a single word."""
    return classify_batch([word])[0][0]


def hard_word_pct(text: str) -> float:
    """Percentage of words in `text` classified as 'Hard'."""
    words = text.split()
    if not words:
        return 0.0
    labels = classify_batch(words)
    hard = sum(1 for label, _ in labels if label == "Hard")
    return round((hard / len(words)) * 100, 1)
