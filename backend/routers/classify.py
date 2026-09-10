"""
backend/routers/classify.py
POST /classify — word difficulty classification (F34).

Serves the trained GradientBoostingClassifier via classifier_service. The
response shape is a fixed contract the Reading page already consumes, so it is
unchanged from M1's placeholder implementation:

    { "results": [ { "word": ..., "label": ..., "confidence": ... } ] }

Two things about that contract are load-bearing:

  * `word` echoes the caller's ORIGINAL token, punctuation and all. ReadingPage
    builds its lookup table by running its own normaliseWord() over this field,
    and its rule differs from the backend's (it strips punctuation everywhere,
    we strip only at the edges). Echoing a cleaned word would silently break
    highlighting for hyphenated words like "well-known".
  * `label` stays one of Easy | Medium | Hard.
"""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from backend.dependencies import get_current_user
from backend.models import User
from backend.services.classifier_service import classify_batch

router = APIRouter(prefix="/classify", tags=["classify"])


# ── request / response schemas ──────────────────────────────────────
class ClassifyRequest(BaseModel):
    # No min_length: an empty list is a valid request that returns an empty
    # result set, not a 422. The Reading page can clear its text at any moment.
    words: List[str] = Field(default_factory=list, max_length=5000)


class WordClassification(BaseModel):
    word: str
    label: str          # "Easy" | "Medium" | "Hard"
    confidence: float   # 0.0 – 1.0


class ClassifyResponse(BaseModel):
    results: List[WordClassification]


# ── endpoint ────────────────────────────────────────────────────────
@router.post("", response_model=ClassifyResponse)
async def classify_words(
    body: ClassifyRequest,
    current_user: User = Depends(get_current_user),
):
    """Classify a list of words as Easy / Medium / Hard.

    No try/except: classify_batch falls back to the wordfreq heuristic when the
    model is missing rather than raising, and swallowing anything else here
    would turn a real bug into a silent 500 with a stack trace in the body —
    which is what the previous implementation did.
    """
    labelled = classify_batch(body.words)
    return ClassifyResponse(
        results=[
            WordClassification(word=word, label=label, confidence=confidence)
            for word, (label, confidence) in zip(body.words, labelled)
        ]
    )
