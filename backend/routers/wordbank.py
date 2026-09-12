"""
backend/routers/wordbank.py
F49/F50 — the personal word bank and its spaced-repetition drill.

    GET  /wordbank              the whole bank with each word's SM-2 state
    GET  /wordbank/drill        the words due today, oldest-due first, max 20
    POST /wordbank/drill/result grade one word and reschedule it

Words are added by session_service as they cross the replay threshold (see
services/wordbank_service); nothing here creates them. Every query is scoped to
the current user.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_serializer
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.dependencies import get_current_user
from backend.models import User, WordBank
from backend.services.classifier_service import normalize_word
from backend.services.sm2_service import (
    MASTERED_EF,
    MASTERED_INTERVAL,
    is_mastered,
    update_sm2,
)
from backend.services.wordbank_service import current_streak, record_drill_day

router = APIRouter()

# The PRD's cap. More than twenty words in a sitting stops being practice.
DRILL_LIMIT = 20


# ── response models ────────────────────────────────────────────────────
class WordBankEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    word: str
    difficulty_label: str | None
    sm2_ef: float
    sm2_interval: int
    sm2_repetitions: int
    next_review: date
    total_drills: int
    last_quality: int | None
    added_at: datetime
    # Derived, not stored — see sm2_service.is_mastered.
    mastered: bool
    due: bool

    @field_serializer("added_at")
    def _stamp_utc(self, value: datetime) -> datetime:
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class WordBankStats(BaseModel):
    words_due_today: int
    # The first few due words, for the homepage reminder. Deliberately a
    # preview: words_due_today is the true total, which the badge shows.
    due_words: list[str]
    total_in_bank: int
    mastered: int
    streak: int


class DrillResultRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    word: str = Field(min_length=1, max_length=100)
    # SM-2's grade. The drill UI offers 1-5; 0 is accepted for completeness.
    quality: int = Field(ge=0, le=5)


class DrillResultOut(BaseModel):
    word: str
    sm2_ef: float
    sm2_interval: int
    sm2_repetitions: int
    next_review: date
    total_drills: int
    last_quality: int
    mastered: bool


def _entry(row: WordBank, today: date) -> WordBankEntry:
    return WordBankEntry(
        word=row.word,
        difficulty_label=row.difficulty_label,
        sm2_ef=row.sm2_ef,
        sm2_interval=row.sm2_interval,
        sm2_repetitions=row.sm2_repetitions,
        next_review=row.next_review,
        total_drills=row.total_drills,
        last_quality=row.last_quality,
        added_at=row.added_at,
        mastered=is_mastered(row.sm2_ef, row.sm2_interval),
        due=row.next_review <= today,
    )


# ── endpoints ──────────────────────────────────────────────────────────
@router.get("/wordbank", response_model=list[WordBankEntry])
def list_word_bank(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """F49 — everything in the bank, soonest review first."""
    today = date.today()
    rows = (
        db.query(WordBank)
        .filter(WordBank.user_id == current_user.id)
        .order_by(WordBank.next_review.asc(), WordBank.word.asc())
        .all()
    )
    return [_entry(row, today) for row in rows]


@router.get("/wordbank/drill", response_model=list[WordBankEntry])
def drill_queue(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """F50 — the words due today.

    Ordered by next_review ascending so that when the cap bites, it is the
    least overdue words that wait rather than the ones furthest behind.
    """
    today = date.today()
    rows = (
        db.query(WordBank)
        .filter(WordBank.user_id == current_user.id, WordBank.next_review <= today)
        .order_by(WordBank.next_review.asc(), WordBank.word.asc())
        .limit(DRILL_LIMIT)
        .all()
    )
    return [_entry(row, today) for row in rows]


@router.get("/wordbank/stats", response_model=WordBankStats)
def word_bank_stats(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """F51 — the numbers behind the nav badge and the homepage reminder.

    words_due_today counts every due word, not the drill's capped 20: the badge
    should say how much is waiting, even when one sitting cannot clear it.
    """
    today = date.today()
    due = [
        row.word
        for row in db.query(WordBank.word)
        .filter(WordBank.user_id == current_user.id, WordBank.next_review <= today)
        .order_by(WordBank.next_review.asc(), WordBank.word.asc())
    ]
    total, mastered = (
        db.query(
            func.count(WordBank.id),
            func.count(WordBank.id).filter(
                WordBank.sm2_ef >= MASTERED_EF, WordBank.sm2_interval >= MASTERED_INTERVAL
            ),
        )
        .filter(WordBank.user_id == current_user.id)
        .one()
    )
    return WordBankStats(
        words_due_today=len(due),
        due_words=due[:5],
        total_in_bank=total,
        mastered=mastered,
        streak=current_streak(db, current_user, today),
    )


@router.post("/wordbank/drill/result", response_model=DrillResultOut)
def record_drill_result(
    req: DrillResultRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """F50 — grade one word and write back its new schedule.

    The word is normalised the same way it was when stored, so a client echoing
    back what it was shown still matches.
    """
    word = normalize_word(req.word)
    row = (
        db.query(WordBank)
        .filter(WordBank.user_id == current_user.id, WordBank.word == word)
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That word is not in your word bank.")

    state = update_sm2(row, req.quality)
    row.sm2_ef = state.sm2_ef
    row.sm2_interval = state.sm2_interval
    row.sm2_repetitions = state.sm2_repetitions
    row.next_review = state.next_review
    row.total_drills += 1
    row.last_quality = req.quality
    # Same transaction as the grade, so a counted answer always counts towards
    # the streak (F51).
    record_drill_day(db, current_user)
    db.commit()
    db.refresh(row)

    return DrillResultOut(
        word=row.word,
        sm2_ef=row.sm2_ef,
        sm2_interval=row.sm2_interval,
        sm2_repetitions=row.sm2_repetitions,
        next_review=row.next_review,
        total_drills=row.total_drills,
        last_quality=row.last_quality,
        mastered=state.mastered,
    )
