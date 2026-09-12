"""
backend/routers/analytics.py
F39-F41 — everything the Analytics page charts.

    GET /analytics/summary          headline numbers across all reading sessions
    GET /analytics/reading          last 20 reading sessions, newest first (F39)
    GET /analytics/writing          last 20 writing sessions, newest first (F40)
    GET /analytics/difficult-words  top 10 words by all-time repeat count (F41)

Every query is scoped to current_user.id. A reader with no history gets empty
lists and a zeroed summary rather than a 404 — "nothing yet" is a normal state
for this page, not an error.

Session lists come newest first, matching the build guide; the page reverses
them so charts read left to right in time.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel, field_serializer
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.dependencies import get_current_user
from backend.models import ReadingSession, User, WordRepeatLog, WritingSession

router = APIRouter()

RECENT_SESSIONS = 20
TOP_DIFFICULT_WORDS = 10


def _utc(value: datetime) -> datetime:
    """SQLite hands timestamps back naive. Mark them UTC so browsers don't read local time."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


# ── response models ────────────────────────────────────────────────────
class SummaryOut(BaseModel):
    reading_sessions: int
    writing_sessions: int
    total_words_read: int
    total_minutes_read: float
    # None until something has been read — a 0 would plot as a real speed.
    avg_wpm: float | None
    best_wpm: float | None


class ReadingPoint(BaseModel):
    id: str
    date: datetime
    wpm: float
    words_read: int
    total_words: int
    duration_seconds: int
    hard_word_count: int
    repeat_count: int
    source_type: str
    simplified: bool
    complexity_score: float | None

    @field_serializer("date")
    def _stamp_utc(self, value: datetime) -> datetime:
        return _utc(value)


class WritingPoint(BaseModel):
    id: str
    date: datetime
    word_count: int
    spell_error_count: int
    grammar_error_count: int
    homophone_flag_count: int
    template_used: str | None
    # Errors per 100 words, the unit the PRD's chart axis uses. None for a
    # session with no words, where a rate would be a division by zero.
    error_rate: float | None

    @field_serializer("date")
    def _stamp_utc(self, value: datetime) -> datetime:
        return _utc(value)


class DifficultWord(BaseModel):
    word: str
    repeat_count: int
    difficulty_label: str | None
    last_seen: datetime

    @field_serializer("last_seen")
    def _stamp_utc(self, value: datetime) -> datetime:
        return _utc(value)


# ── helpers ────────────────────────────────────────────────────────────
def _words_read(row: ReadingSession) -> int:
    """Words reached in the session.

    Not stored directly, but recoverable exactly: session_service computes wpm
    as words_read / minutes, so words_read = wpm x minutes (to the rounding of
    the stored wpm, which is to one decimal place).
    """
    return round(row.wpm * row.duration_seconds / 60)


def _error_rate(row: WritingSession) -> float | None:
    if row.word_count <= 0:
        return None
    errors = row.spell_error_count + row.grammar_error_count + row.homophone_flag_count
    return round(errors / row.word_count * 100, 1)


# ── endpoints ──────────────────────────────────────────────────────────
@router.get("/analytics/summary", response_model=SummaryOut)
def summary(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Headline numbers for the stat tiles.

    Average WPM is weighted by time — total words read over total minutes —
    rather than a mean of per-session speeds, so a 30-second session cannot
    count as much as a 20-minute one.
    """
    count, seconds, words, best = (
        db.query(
            func.count(ReadingSession.id),
            func.coalesce(func.sum(ReadingSession.duration_seconds), 0),
            func.coalesce(func.sum(ReadingSession.wpm * ReadingSession.duration_seconds / 60.0), 0),
            func.max(ReadingSession.wpm),
        )
        .filter(ReadingSession.user_id == current_user.id)
        .one()
    )
    writing = (
        db.query(func.count(WritingSession.id))
        .filter(WritingSession.user_id == current_user.id)
        .scalar()
    )

    minutes = seconds / 60
    return SummaryOut(
        reading_sessions=count,
        writing_sessions=writing,
        total_words_read=round(words),
        total_minutes_read=round(minutes, 1),
        avg_wpm=round(words / minutes, 1) if minutes > 0 else None,
        best_wpm=best,
    )


@router.get("/analytics/reading", response_model=list[ReadingPoint])
def reading_history(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """F39 — the last 20 reading sessions, for the WPM trend."""
    rows = (
        db.query(ReadingSession)
        .filter(ReadingSession.user_id == current_user.id)
        .order_by(ReadingSession.date.desc())
        .limit(RECENT_SESSIONS)
        .all()
    )
    return [
        ReadingPoint(
            id=row.id,
            date=row.date,
            wpm=row.wpm,
            words_read=_words_read(row),
            total_words=row.total_words,
            duration_seconds=row.duration_seconds,
            hard_word_count=row.hard_word_count,
            repeat_count=row.repeat_count,
            source_type=row.source_type,
            simplified=row.simplified,
            complexity_score=row.complexity_score,
        )
        for row in rows
    ]


@router.get("/analytics/writing", response_model=list[WritingPoint])
def writing_history(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """F40 — the last 20 writing sessions, for the error-rate chart."""
    rows = (
        db.query(WritingSession)
        .filter(WritingSession.user_id == current_user.id)
        .order_by(WritingSession.date.desc())
        .limit(RECENT_SESSIONS)
        .all()
    )
    return [
        WritingPoint(
            id=row.id,
            date=row.date,
            word_count=row.word_count,
            spell_error_count=row.spell_error_count,
            grammar_error_count=row.grammar_error_count,
            homophone_flag_count=row.homophone_flag_count,
            template_used=row.template_used,
            error_rate=_error_rate(row),
        )
        for row in rows
    ]


@router.get("/analytics/difficult-words", response_model=list[DifficultWord])
def difficult_words(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """F41 — the words this reader has most often asked to hear again.

    Ties go to the most recently replayed, so the list favours what the reader
    is struggling with now over something settled long ago.
    """
    rows = (
        db.query(WordRepeatLog)
        .filter(WordRepeatLog.user_id == current_user.id)
        .order_by(WordRepeatLog.repeat_count.desc(), WordRepeatLog.last_seen.desc())
        .limit(TOP_DIFFICULT_WORDS)
        .all()
    )
    return [
        DifficultWord(
            word=row.word,
            repeat_count=row.repeat_count,
            difficulty_label=row.difficulty_label,
            last_seen=row.last_seen,
        )
        for row in rows
    ]
