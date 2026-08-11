"""
backend/routers/nlp.py
F26 phonetic spell correction · F27 grammar · F28 homophone detection
F29 word prediction · v5.0 phrase completion

Endpoints: POST /nlp/check, POST /nlp/predict

/nlp/check runs all three checks over a single spaCy parse — the writing notepad
debounces at 800ms and would otherwise fire three requests per pause.

/nlp/predict is separate because it answers a different question at a different
rhythm: it needs only the text before the caret and has to feel immediate, so it
is debounced far more tightly than the checks.
"""

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from backend.dependencies import get_current_user
from backend.models import User
from backend.services import nlp_service, prediction_service

router = APIRouter()


class CheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(max_length=nlp_service.MAX_CHECK_CHARS)


class Issue(BaseModel):
    type: Literal["spelling", "grammar", "homophone"]
    start: int  # character offsets into the submitted text
    end: int
    text: str
    message: str
    suggestions: list[str]


class CheckResponse(BaseModel):
    issues: list[Issue]
    counts: dict[str, int]
    # False while the JVM is still warming, or permanently when Java is absent.
    # Spelling and homophones still work either way, so the client can show what
    # it has instead of failing the whole check.
    grammar_available: bool
    grammar_unavailable_reason: str | None = None
    # False when spaCy itself can't load, which disables all three checks. The
    # notepad keeps working; it just stops showing suggestions.
    checks_available: bool = True
    checks_unavailable_reason: str | None = None


class PredictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Only the text before the caret — the model predicts what comes next, so
    # anything after it is irrelevant.
    text: str = Field(max_length=nlp_service.MAX_CHECK_CHARS)


class PredictResponse(BaseModel):
    words: list[str]      # up to three single-word pills (F29)
    phrase: str           # a longer completion, "" when there isn't a good one
    available: bool
    unavailable_reason: str | None = None


@router.post("/nlp/check", response_model=CheckResponse)
def check(req: CheckRequest, current_user: User = Depends(get_current_user)):
    """F26-F28 — every issue found in the text, sorted by position."""
    return nlp_service.check_text(req.text)


@router.post("/nlp/predict", response_model=PredictResponse)
def predict(req: PredictRequest, current_user: User = Depends(get_current_user)):
    """
    F29 + v5.0 — what could come next at the caret.

    Never fails: prediction_service swallows its own errors and returns empty
    results, because a suggestion that can't be produced is not worth turning a
    keystroke into an error.
    """
    return prediction_service.predict(req.text)
