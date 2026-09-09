"""
backend/services/classifier_service.py
Shared word-difficulty classification logic.

Both /classify (per-word labels shown live in the Reading page) and
simplification_service (before/after hard-word % for /reading/simplify
and /reading/complexity) need to answer "is this word hard?" — they used
to do it two different ways (wordfreq thresholds vs. a >8-characters
heuristic), so the same text could show two disagreeing hard-word
percentages on screen at once. This module is the single source of
truth so every caller agrees on the same definition of "hard."

Placeholder heuristic — wordfreq frequency bands only, no syllable count
or word length. Slated for replacement by M3's trained MRC-based
classifier (PRD F33-F35); until then, don't rely on these labels being
precisely calibrated (see the M1->M2 handoff notes on this).
"""

import re

from nltk.tokenize import SyllableTokenizer
from wordfreq import word_frequency

# frequency > 1e-4          -> Easy
# 1e-6 <= frequency <= 1e-4 -> Medium
# frequency < 1e-6          -> Hard
EASY_THRESHOLD = 1e-4
MEDIUM_THRESHOLD = 1e-6


def classify_word_label(word: str) -> str:
    """Return 'Easy' | 'Medium' | 'Hard' for a single word."""
    clean = word.strip().lower()
    if not clean:
        return "Easy"

    freq = word_frequency(clean, "en")
    if freq > EASY_THRESHOLD:
        return "Easy"
    elif freq >= MEDIUM_THRESHOLD:
        return "Medium"
    else:
        return "Hard"


def hard_word_pct(text: str) -> float:
    """Percentage of words in `text` classified as 'Hard'."""
    words = text.split()
    if not words:
        return 0.0
    hard = sum(1 for w in words if classify_word_label(w) == "Hard")
    return round((hard / len(words)) * 100, 1)


# ── feature extraction for the trained classifier (F33) ─────────────
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
    """Lowercase and strip edge punctuation. May return ''."""
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
