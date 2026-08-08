"""
backend/routers/reading.py
Endpoints: /reading/simplify, /reading/complexity, /reading/define
Task 2 — syllable count now uses NLTK CMU Pronouncing Dictionary,
         falling back to vowel heuristic only when word is absent.
Also enriches /reading/define response with all meanings + syllable_count.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from backend.dependencies import get_current_user
from backend.models import User
from backend.services import simplification_service
import httpx
import re

# ── CMU dict for accurate syllable counts (Task 2) ─────────────────
import nltk
from nltk.corpus import cmudict as _cmudict_module

try:
    _cmu = _cmudict_module.dict()
except LookupError:
    nltk.download("cmudict", quiet=True)
    _cmu = _cmudict_module.dict()


router = APIRouter()


class SimplifyRequest(BaseModel):
    text: str


class ComplexityRequest(BaseModel):
    text: str


class DefineRequest(BaseModel):
    word: str


# ── syllable helpers (Task 2) ──────────────────────────────────────
def _syllables_cmu(word: str):
    """Return syllable count from CMU dict, or None if word not found."""
    pronunciations = _cmu.get(word.lower().strip())
    if not pronunciations:
        return None
    # Each phoneme that ends with a digit represents a vowel nucleus
    return sum(1 for phoneme in pronunciations[0] if phoneme[-1].isdigit())


def _syllables_vowel(word: str) -> int:
    """Fallback heuristic: count vowel groups."""
    word = word.lower().strip()
    if not word:
        return 0
    count = len(re.findall(r'[aeiouy]+', word))
    # silent-e adjustment
    if word.endswith('e') and count > 1:
        count -= 1
    return max(1, count)


def count_syllables(word: str) -> int:
    """Accurate syllable count: CMU dict first, vowel fallback second."""
    cmu_count = _syllables_cmu(word)
    if cmu_count is not None:
        return cmu_count
    return _syllables_vowel(word)


# ── endpoints ──────────────────────────────────────────────────────

@router.post("/reading/simplify")
async def simplify(
    req: SimplifyRequest,
    current_user: User = Depends(get_current_user)
):
    if not req.text.strip():
        raise HTTPException(400, "No text provided.")
    return await simplification_service.simplify_text(req.text)


@router.post("/reading/complexity")
async def complexity(
    req: ComplexityRequest,
    current_user: User = Depends(get_current_user)
):
    if not req.text.strip():
        raise HTTPException(400, "No text provided.")
    return await simplification_service.get_complexity(req.text)


@router.post("/reading/define")
async def define_word(
    req: DefineRequest,
    current_user: User = Depends(get_current_user)
):
    word = req.word.strip().lower()
    if not word:
        raise HTTPException(400, "No word provided.")

    # ── Task 2: accurate syllable count ────────────────────────────
    syllable_count = count_syllables(word)

    # ── dictionary API lookup ──────────────────────────────────────
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"https://api.dictionaryapi.dev/api/v2/entries/en/{word}",
            timeout=5.0
        )

    if response.status_code == 404:
        raise HTTPException(404, "Definition not found. Try a different form of the word.")

    data = response.json()
    entry = data[0]

    # Extract phonetic
    phonetic = entry.get("phonetic", "")
    if not phonetic and entry.get("phonetics"):
        for ph in entry.get("phonetics", []):
            if ph.get("text"):
                phonetic = ph["text"]
                break

    # Extract first definition and example (kept for backward compat)
    definition = ""
    example = ""

    # Also build full meanings list for richer AC-34 response
    meanings_raw = entry.get("meanings", [])
    meanings_out = []

    for m in meanings_raw:
        part_of_speech = m.get("partOfSpeech", "")
        defs_raw = m.get("definitions", [])
        defs_out = []

        for d in defs_raw:
            def_entry = {"definition": d.get("definition", "")}
            if d.get("example"):
                def_entry["example"] = d["example"]
                # capture first example we find
                if not example:
                    example = d["example"]
            defs_out.append(def_entry)

        # capture first definition we find
        if not definition and defs_out:
            definition = defs_out[0].get("definition", "")

        meanings_out.append({
            "partOfSpeech": part_of_speech,
            "definitions": defs_out
        })

    return {
        "word": word,
        "phonetic": phonetic,
        "definition": definition,
        "example": example,
        "syllable_count": syllable_count,
        "syllables": syllable_count,       # kept for backward compat
        "meanings": meanings_out           # full meanings for AC-34
    }