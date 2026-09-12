"""
backend/models.py
SQLAlchemy models: users, saved_documents and writing_sessions (M2), and
reading_sessions, word_repeat_log and word_bank (M3).

Adapted from PRD Section 4 (PostgreSQL 15) to SQLite:
  - UUID PK           -> String(36) holding str(uuid4()), generated in Python
  - TIMESTAMP (UTC)   -> DateTime with a timezone-aware UTC default
  - VARCHAR(n)/BOOLEAN map directly

The three M3 tables are new tables rather than new columns on existing ones, so
create_all() builds them on an existing leximind.db with no _ADDED_COLUMNS entry.

Preference defaults follow the frontend's actual DEFAULTS in
frontend/src/hooks/usePreferences.js, which have drifted from the PRD's stale
values (font 'Lexend' not 'Arial', focus ruler on by default). pref_word_spacing
is not in the PRD but exists in the frontend, so it is stored too — otherwise the
Phase 2 localStorage migration would lose it.
"""

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from backend.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _today() -> date:
    """Today in UTC — the day a new word bank entry is first due."""
    return datetime.now(timezone.utc).date()


class User(Base):
    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=_uuid)
    name = Column(String(100), nullable=False)
    email = Column(String(255), unique=True, nullable=False, index=True)  # stored lowercase
    password_hash = Column(String(255), nullable=False)  # bcrypt cost 12, never returned via API
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    last_active = Column(DateTime, nullable=False, default=_utcnow)
    # Set whenever the password changes. Access tokens issued before this moment
    # are refused, so a password reset actually ends whatever sessions were open
    # — which is the point of resetting it when someone else has your account.
    password_changed_at = Column(DateTime, nullable=False, default=_utcnow)

    # ── preferences (PRD 4: pref_* columns) ────────────────────────────
    pref_font = Column(String(50), nullable=False, default="Lexend")
    pref_overlay = Column(String(7), nullable=False, default="#FFFFFF")
    pref_tts_speed = Column(Float, nullable=False, default=1.0)          # 0.5–2.0
    pref_font_size = Column(Integer, nullable=False, default=18)         # 14–28
    pref_line_spacing = Column(Float, nullable=False, default=2.0)       # 1.5–3.0
    pref_word_spacing = Column(Integer, nullable=False, default=2)       # frontend-only, px
    pref_dark_mode = Column(Boolean, nullable=False, default=False)
    pref_highlight_color = Column(String(7), nullable=False, default="#FFD700")
    pref_focus_ruler = Column(Boolean, nullable=False, default=True)
    pref_distraction_free = Column(Boolean, nullable=False, default=False)
    pref_phrase_pauses = Column(Boolean, nullable=False, default=True)

    documents = relationship(
        "SavedDocument", back_populates="user", cascade="all, delete-orphan"
    )
    writing_sessions = relationship(
        "WritingSession", back_populates="user", cascade="all, delete-orphan"
    )
    reset_tokens = relationship(
        "PasswordResetToken", back_populates="user", cascade="all, delete-orphan"
    )
    reading_sessions = relationship(
        "ReadingSession", back_populates="user", cascade="all, delete-orphan"
    )
    word_repeats = relationship(
        "WordRepeatLog", back_populates="user", cascade="all, delete-orphan"
    )
    word_bank = relationship(
        "WordBank", back_populates="user", cascade="all, delete-orphan"
    )
    drill_days = relationship(
        "DrillDay", back_populates="user", cascade="all, delete-orphan"
    )


class SavedDocument(Base):
    """A named writing-notepad document. Also backs autosave (F31)."""

    __tablename__ = "saved_documents"

    id = Column(String(36), primary_key=True, default=_uuid)
    user_id = Column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title = Column(String(150), nullable=False, default="Untitled")
    content = Column(Text, nullable=False, default="")  # capped at 50,000 chars by the API
    template = Column(String(50), nullable=True)        # 'essay' | 'email' | 'report' | None
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    updated_at = Column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)

    user = relationship("User", back_populates="documents")


class PasswordResetToken(Base):
    """A single-use, expiring ticket to set a new password.

    Only the SHA-256 of the token is stored. The plaintext exists just long
    enough to be put in the reset link, so a copy of this table is not a set of
    working reset links — the same reason `password_hash` is a hash.
    """

    __tablename__ = "password_reset_tokens"

    id = Column(String(36), primary_key=True, default=_uuid)
    user_id = Column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash = Column(String(64), unique=True, nullable=False, index=True)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)  # set on redemption; never reusable
    created_at = Column(DateTime, nullable=False, default=_utcnow)

    user = relationship("User", back_populates="reset_tokens")


class WritingSession(Base):
    """Per-session writing analytics. Counters only — the text lives in SavedDocument."""

    __tablename__ = "writing_sessions"

    id = Column(String(36), primary_key=True, default=_uuid)
    user_id = Column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    date = Column(DateTime, nullable=False, default=_utcnow)  # session start, UTC
    word_count = Column(Integer, nullable=False, default=0)
    spell_error_count = Column(Integer, nullable=False, default=0)
    grammar_error_count = Column(Integer, nullable=False, default=0)
    homophone_flag_count = Column(Integer, nullable=False, default=0)
    template_used = Column(String(50), nullable=True)

    user = relationship("User", back_populates="writing_sessions")


class ReadingSession(Base):
    """F37 — one stretch of listening to one loaded text.

    Only sessions with at least 30 seconds of active playback are stored; see
    backend/services/session_service.py for why, and for what happens to the
    word replays of shorter ones.
    """

    __tablename__ = "reading_sessions"

    id = Column(String(36), primary_key=True, default=_uuid)
    user_id = Column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    date = Column(DateTime, nullable=False, default=_utcnow)           # session start, UTC
    wpm = Column(Float, nullable=False, default=0.0)                   # words reached per active minute
    total_words = Column(Integer, nullable=False, default=0)
    hard_word_count = Column(Integer, nullable=False, default=0)       # tokens labelled Hard
    repeat_count = Column(Integer, nullable=False, default=0)          # word replays this session
    duration_seconds = Column(Integer, nullable=False, default=0)      # active playback, pauses excluded
    source_type = Column(String(10), nullable=False, default="paste")  # image | pdf | paste | sample
    simplified = Column(Boolean, nullable=False, default=False)
    complexity_score = Column(Float, nullable=True)                    # Flesch-Kincaid grade at start

    user = relationship("User", back_populates="reading_sessions")


class WordRepeatLog(Base):
    """F41/F49 — how often a reader has asked to hear each word, across all sessions."""

    __tablename__ = "word_repeat_log"
    # One row per word per reader, enforced rather than merely expected.
    __table_args__ = (UniqueConstraint("user_id", "word", name="uq_word_repeat_log_user_word"),)

    id = Column(String(36), primary_key=True, default=_uuid)
    user_id = Column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    word = Column(String(100), nullable=False)                   # normalised, lowercase
    repeat_count = Column(Integer, nullable=False, default=0)    # cumulative across all sessions
    difficulty_label = Column(String(10), nullable=True)         # classifier label at last repeat
    last_seen = Column(DateTime, nullable=False, default=_utcnow)

    user = relationship("User", back_populates="word_repeats")


class WordBank(Base):
    """F49-F51 — a reader's personal practice list, scheduled by SM-2.

    Created alongside the other M3 tables so the schema lands in one piece;
    nothing writes to it until the word bank features are built.
    """

    __tablename__ = "word_bank"
    __table_args__ = (UniqueConstraint("user_id", "word", name="uq_word_bank_user_word"),)

    id = Column(String(36), primary_key=True, default=_uuid)
    user_id = Column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    word = Column(String(100), nullable=False)                     # normalised, lowercase
    difficulty_label = Column(String(10), nullable=True)           # latest classifier label
    sm2_ef = Column(Float, nullable=False, default=2.5)            # easiness factor, never below 1.3
    sm2_interval = Column(Integer, nullable=False, default=1)      # days until the next review
    sm2_repetitions = Column(Integer, nullable=False, default=0)   # consecutive correct; resets on a miss
    next_review = Column(Date, nullable=False, default=_today)     # drives the drill-due badge (F51)
    total_drills = Column(Integer, nullable=False, default=0)
    last_quality = Column(Integer, nullable=True)                  # last SM-2 grade, 0-5
    added_at = Column(DateTime, nullable=False, default=_utcnow)

    user = relationship("User", back_populates="word_bank")


class DrillDay(Base):
    """F51 — one row per day a reader practised, for the streak.

    word_bank holds only each word's latest state, so it cannot say which days
    had practice: drilling a word again overwrites the evidence of the previous
    time. This is the smallest record that can answer "how many days in a row",
    and it leaves M4 a real practice history rather than a derived guess.

    The day comes from the server's clock, so a reader drilling either side of
    midnight in a distant timezone can see a day land on the wrong side. The
    stakes are a reminder card's counter, which is not worth carrying a
    timezone per account for.
    """

    __tablename__ = "drill_days"
    __table_args__ = (UniqueConstraint("user_id", "day", name="uq_drill_days_user_day"),)

    id = Column(String(36), primary_key=True, default=_uuid)
    user_id = Column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    day = Column(Date, nullable=False, default=_today)
    words_drilled = Column(Integer, nullable=False, default=0)

    user = relationship("User", back_populates="drill_days")
