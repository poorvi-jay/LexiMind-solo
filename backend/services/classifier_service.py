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
