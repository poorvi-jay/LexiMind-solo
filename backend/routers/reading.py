"""
backend/routers/reading.py
Endpoints: /reading/simplify, /reading/complexity, /reading/define
Task 2 — syllable count uses NLTK CMU Pronouncing Dictionary via
         backend/services/syllables.py, vowel heuristic as fallback.
Also enriches /reading/define response with all meanings + syllable_count.
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from backend.dependencies import get_current_user
from backend.models import User
from backend.services import simplification_service
from backend.services.syllables import clean_word, count_syllables, split_syllables
from functools import lru_cache
import httpx
import nltk


# ── WordNet: offline definitions when dictionaryapi.dev is down ────
# Loaded on first use (not at import) to keep backend startup fast.
@lru_cache(maxsize=1)
def _wordnet():
    from nltk.corpus import wordnet
    try:
        wordnet.ensure_loaded()
    except LookupError:
        nltk.download("wordnet", quiet=True)
        nltk.download("omw-1.4", quiet=True)
        wordnet.ensure_loaded()
    return wordnet


_WN_POS = {"n": "noun", "v": "verb", "a": "adjective", "s": "adjective", "r": "adverb"}


router = APIRouter()


class SimplifyRequest(BaseModel):
    text: str


class ComplexityRequest(BaseModel):
    text: str


class DefineRequest(BaseModel):
    word: str


class SyllabifyRequest(BaseModel):
    words: list[str] = Field(default_factory=list, max_length=5000)


def _wordnet_meanings(word: str, max_per_pos: int = 3):
    """Meanings in the dictionaryapi.dev shape, or [] if WordNet lacks the word."""
    grouped = {}
    for synset in _wordnet().synsets(word):
        pos = _WN_POS.get(synset.pos(), synset.pos())
        defs = grouped.setdefault(pos, [])
        if len(defs) >= max_per_pos:
            continue
        entry = {"definition": synset.definition()}
        if synset.examples():
            entry["example"] = synset.examples()[0]
        defs.append(entry)
    return [{"partOfSpeech": pos, "definitions": defs} for pos, defs in grouped.items()]


def _build_definition(word, phonetic, meanings, syllable_count, source, syllable_parts):
    definition = next((d["definition"] for m in meanings for d in m["definitions"]), "")
    example = next(
        (d["example"] for m in meanings for d in m["definitions"] if d.get("example")), ""
    )
    return {
        "word": word,
        "phonetic": phonetic,
        "definition": definition,
        "example": example,
        "syllable_count": syllable_count,
        "syllables": syllable_count,       # kept for backward compat
        "syllable_parts": syllable_parts,  # ["chlo", "ro", "plasts"]
        "meanings": meanings,              # full meanings for AC-34
        "source": source,
    }


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


@router.post("/reading/syllabify")
async def syllabify(
    req: SyllabifyRequest,
    current_user: User = Depends(get_current_user)
):
    """Syllable breakdown for a batch of words, so the reading page can show
    a whole page at once instead of one request per word.
    Returns { results: { "photosynthesis": ["pho","to","syn","the","sis"] } }
    keyed by the cleaned (lowercase, punctuation-free) word."""
    results = {}
    for word in req.words:
        key = clean_word(word)
        if key and key not in results:
            parts = split_syllables(word)
            if parts:
                results[key] = list(parts)
    return {"results": results}


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
    syllable_parts = list(split_syllables(word))

    # ── online dictionary (phonetics + richer data) ────────────────
    # dictionaryapi.dev is free and often slow or down, so keep the
    # timeout short and fall back to offline WordNet on any failure.
    entry = None
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(
                f"https://api.dictionaryapi.dev/api/v2/entries/en/{word}"
            )
        if response.status_code == 200:
            entry = response.json()[0]
    except (httpx.HTTPError, ValueError, IndexError, KeyError, TypeError):
        entry = None

    if entry is None:
        # Offline fallback. morphy maps inflections ("studies" → "study").
        meanings = _wordnet_meanings(word) or _wordnet_meanings(_wordnet().morphy(word) or word)
        if not meanings:
            raise HTTPException(404, "Definition not found. Try a different form of the word.")
        return _build_definition(word, "", meanings, syllable_count, "wordnet", syllable_parts)

    # Extract phonetic
    phonetic = entry.get("phonetic", "")
    if not phonetic:
        phonetic = next((ph["text"] for ph in entry.get("phonetics", []) if ph.get("text")), "")

    meanings = []
    for m in entry.get("meanings", []):
        defs_out = []
        for d in m.get("definitions", []):
            def_entry = {"definition": d.get("definition", "")}
            if d.get("example"):
                def_entry["example"] = d["example"]
            defs_out.append(def_entry)
        meanings.append({"partOfSpeech": m.get("partOfSpeech", ""), "definitions": defs_out})

    return _build_definition(word, phonetic, meanings, syllable_count, "dictionaryapi",
                             syllable_parts)