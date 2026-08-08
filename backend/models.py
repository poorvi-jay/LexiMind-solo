"""
backend/models.py
SQLAlchemy models for the three tables M2 needs: users, saved_documents,
writing_sessions.

Adapted from PRD Section 4 (PostgreSQL 15) to SQLite:
  - UUID PK           -> String(36) holding str(uuid4()), generated in Python
  - TIMESTAMP (UTC)   -> DateTime with a timezone-aware UTC default
  - VARCHAR(n)/BOOLEAN map directly

The other three PRD tables (reading_sessions, word_repeat_log, word_bank) belong
to the analytics/word-bank module and are deliberately not created here.

Preference defaults follow the frontend's actual DEFAULTS in
frontend/src/hooks/usePreferences.js, which have drifted from the PRD's stale
values (font 'Lexend' not 'Arial', focus ruler on by default). pref_word_spacing
is not in the PRD but exists in the frontend, so it is stored too — otherwise the
Phase 2 localStorage migration would lose it.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from backend.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=_uuid)
    name = Column(String(100), nullable=False)
    email = Column(String(255), unique=True, nullable=False, index=True)  # stored lowercase
    password_hash = Column(String(255), nullable=False)  # bcrypt cost 12, never returned via API
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    last_active = Column(DateTime, nullable=False, default=_utcnow)

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
