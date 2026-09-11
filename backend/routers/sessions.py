"""
backend/routers/sessions.py
F37 reading-session and F38 writing-session logging.

    POST /sessions/reading   log a finished reading session and its word replays
    POST /sessions/writing   upsert the current writing session's counters

F38 began as PATCH /writing/session — M2's F48 work, written as a stand-in for
exactly this endpoint with deliberately the same payload. It lives here now;
routers/writing.py keeps the old path registered on this same handler, marked
deprecated, so nothing calling it breaks and nothing is counted twice.

Both endpoints are analytics and the clients swallow every failure, so errors
here are for developers, not readers.
"""

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.dependencies import get_current_user
from backend.models import User, WritingSession
from backend.services.session_service import log_reading_session

router = APIRouter()

# Distinct words per report. Also keeps the repeat log's IN (...) query well
# under SQLite's bound-parameter limit.
MAX_REPEAT_WORDS = 500
MAX_REPEATS_PER_WORD = 100


# ── F37 · reading sessions ─────────────────────────────────────────────
class ReadingSessionRequest(BaseModel):
    """One finished reading session, reported once by the client."""

    model_config = ConfigDict(extra="forbid")

    # When playback first started. Optional, and sanity-checked by the service.
    started_at: datetime | None = None
    # Active playback only — the client excludes paused time.
    duration_seconds: int = Field(ge=0, le=86_400)
    # Furthest word reached, for WPM. Capped at total_words by the service.
    words_read: int = Field(ge=0, le=100_000)
    total_words: int = Field(ge=0, le=100_000)
    hard_word_count: int = Field(ge=0, le=100_000)
    # The PRD lists image | pdf | paste. 'sample' is added so the built-in demo
    # passage can be told apart from a reader's own material in the analytics.
    source_type: Literal["image", "pdf", "paste", "sample"]
    simplified: bool = False
    complexity_score: float | None = None  # Flesch-Kincaid grade at reading start
    # word -> times it was tapped to hear again during this session
    word_repeats: dict[str, int] = Field(default_factory=dict)

    @field_validator("word_repeats")
    @classmethod
    def _bounded(cls, value: dict[str, int]) -> dict[str, int]:
        if len(value) > MAX_REPEAT_WORDS:
            raise ValueError(f"at most {MAX_REPEAT_WORDS} distinct words per session")
        for word, count in value.items():
            if not 1 <= count <= MAX_REPEATS_PER_WORD:
                raise ValueError(
                    f"repeat count for {word!r} must be between 1 and {MAX_REPEATS_PER_WORD}"
                )
        return value


class ReadingSessionOut(BaseModel):
    # False for a session under the 30-second minimum. Its word replays are
    # still logged, so this can be false while words_logged is not zero.
    session_logged: bool
    session_id: str | None
    wpm: float | None
    words_logged: int


@router.post("/sessions/reading", response_model=ReadingSessionOut)
def log_reading(
    req: ReadingSessionRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """F37 — log a reading session. See services/session_service for the rules."""
    result = log_reading_session(db, current_user, **req.model_dump())
    session = result.session
    return ReadingSessionOut(
        session_logged=session is not None,
        session_id=session.id if session else None,
        wpm=session.wpm if session else None,
        words_logged=len(result.repeat_totals),
    )


# ── F38 · writing sessions ─────────────────────────────────────────────
class WritingSessionRequest(BaseModel):
    """The counters for one stretch of writing.

    Counts are absolute totals for the session, not deltas, so a repeated or
    out-of-order report settles on the same row rather than compounding.
    """

    model_config = ConfigDict(extra="forbid")

    # Absent on the first report of a session; the id comes back and is reused.
    session_id: str | None = None
    word_count: int = Field(ge=0)
    spell_error_count: int = Field(default=0, ge=0)
    grammar_error_count: int = Field(default=0, ge=0)
    homophone_flag_count: int = Field(default=0, ge=0)
    template_used: Literal["essay", "email", "report"] | None = None


class WritingSessionOut(BaseModel):
    id: str
    date: datetime
    word_count: int
    spell_error_count: int
    grammar_error_count: int
    homophone_flag_count: int
    template_used: str | None

    @field_serializer("date")
    def _stamp_utc(self, value: datetime) -> datetime:
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _owned_writing_session(session_id: str, user: User, db: Session) -> WritingSession:
    """Fetch a session row or 404 — identically for missing and for another user's."""
    row = db.get(WritingSession, session_id)
    if row is None or row.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Writing session not found.")
    return row


@router.post("/sessions/writing", response_model=WritingSessionOut)
def log_writing_session(
    req: WritingSessionRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """F38 — record the session's counters, including F48's template_used.

    Upsert: no id creates the row and returns one, an id updates it. `date` is
    the moment the session was first reported and never moves, so it stays the
    session's start rather than its last update.
    """
    if req.session_id:
        row = _owned_writing_session(req.session_id, current_user, db)
    else:
        # A page that was opened and left has nothing worth a row.
        if req.word_count == 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nothing written yet.")
        row = WritingSession(user_id=current_user.id)
        db.add(row)

    row.word_count = req.word_count
    row.spell_error_count = req.spell_error_count
    row.grammar_error_count = req.grammar_error_count
    row.homophone_flag_count = req.homophone_flag_count
    # null means "no template" rather than "no change": the client always knows
    # what the notepad was started from, and a writer who clears the dropdown
    # should not leave a stale label on the row.
    row.template_used = req.template_used

    db.commit()
    db.refresh(row)

    return WritingSessionOut(
        id=row.id,
        date=row.date,
        word_count=row.word_count,
        spell_error_count=row.spell_error_count,
        grammar_error_count=row.grammar_error_count,
        homophone_flag_count=row.homophone_flag_count,
        template_used=row.template_used,
    )
