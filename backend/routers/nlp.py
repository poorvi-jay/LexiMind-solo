"""
backend/routers/nlp.py
F26 phonetic spell correction · F27 grammar · F28 homophone detection

Endpoint: POST /nlp/check

One endpoint runs all three checks over a single spaCy parse — the writing
notepad debounces at 800ms and would otherwise fire three requests per pause.
"""

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from backend.dependencies import get_current_user
from backend.models import User
from backend.services import nlp_service

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


@router.post("/nlp/check", response_model=CheckResponse)
def check(req: CheckRequest, current_user: User = Depends(get_current_user)):
    """F26-F28 — every issue found in the text, sorted by position."""
    return nlp_service.check_text(req.text)
