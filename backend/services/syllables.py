"""
backend/services/syllables.py
Shared syllable helpers:
  * count_syllables() — CMU Pronouncing Dictionary first, vowel-group
    heuristic when the word is absent (used by /reading/define and the
    complexity / reading-level metrics).
  * split_syllables() — syllable breakdown for the reading page's syllable
    view, via Pyphen's US English hyphenation patterns.
Both data sources load on first use so they don't slow backend startup.
"""

import re
from functools import lru_cache

import nltk


@lru_cache(maxsize=1)
def _cmu():
    from nltk.corpus import cmudict
    try:
        return cmudict.dict()
    except LookupError:
        nltk.download("cmudict", quiet=True)
        return cmudict.dict()


def _syllables_cmu(word: str):
    """Return syllable count from CMU dict, or None if word not found."""
    pronunciations = _cmu().get(word)
    if not pronunciations:
        return None
    # Each phoneme that ends with a digit represents a vowel nucleus
    return sum(1 for phoneme in pronunciations[0] if phoneme[-1].isdigit())


def _syllables_vowel(word: str) -> int:
    """Fallback heuristic: count vowel groups."""
    count = len(re.findall(r'[aeiouy]+', word))
    # silent-e adjustment
    if word.endswith('e') and count > 1:
        count -= 1
    return max(1, count)


@lru_cache(maxsize=1)
def _hyphenator():
    """Pyphen with US English patterns — real hyphenation dictionaries give
    conventional breaks ("pho-to-syn-the-sis"). None if pyphen isn't
    installed, in which case split_syllables() falls back to NLTK."""
    try:
        import pyphen
        return pyphen.Pyphen(lang="en_US")
    except Exception:
        return None


@lru_cache(maxsize=1)
def _tokenizer():
    """NLTK's sonority-sequencing syllabifier — the fallback, and the same one
    the difficulty classifier and the Word Bank drill use."""
    from nltk.tokenize import SyllableTokenizer
    return SyllableTokenizer()


def clean_word(word: str) -> str:
    """Lowercase letters (and inner apostrophes) only — "Cells," -> "cells".
    The frontend uses the same rule to look syllables up."""
    return re.sub(r"[^a-z']", "", word.lower()).strip("'")


def _merge_vowelless(parts):
    """A chunk with no vowel isn't a syllable — merge it into its neighbour
    ("chloro|plas|ts" -> "chloro|plasts")."""
    merged = []
    for part in parts:
        if merged and (not re.search(r"[aeiouy]", part)
                       or not re.search(r"[aeiouy]", merged[-1])):
            merged[-1] += part
        else:
            merged.append(part)
    return tuple(merged)


@lru_cache(maxsize=20_000)
def split_syllables(word: str) -> tuple:
    """Split one word into syllables ("photosynthesis" -> pho|to|syn|the|sis).
    Punctuation is ignored; returns () for tokens with no letters."""
    clean = clean_word(word)
    if not clean:
        return ()

    parts = []
    hyphenator = _hyphenator()
    if hyphenator is not None:
        cuts = [0, *hyphenator.positions(clean), len(clean)]
        parts = [clean[a:b] for a, b in zip(cuts, cuts[1:]) if clean[a:b]]
    if not parts:
        try:
            parts = [p for p in _tokenizer().tokenize(clean) if p]
        except Exception:
            parts = []
    return _merge_vowelless(parts) or (clean,)


def count_syllables(word: str) -> int:
    """Accurate syllable count: CMU dict first, vowel fallback second.
    Punctuation is ignored ("sunlight," -> "sunlight"); returns 0 for
    tokens with no letters."""
    word = clean_word(word)
    if not word:
        return 0
    cmu_count = _syllables_cmu(word)
    if cmu_count is not None:
        return cmu_count
    return _syllables_vowel(word)
