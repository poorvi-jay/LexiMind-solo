"""
backend/services/session_service.py
F37 reading-session logging, and the per-word replay counts behind F41/F49.

A reading session is one stretch of listening to one loaded text. The client
reports it once, when it ends (Stop, the audio finishing, new text, or leaving
the page), together with the words the reader tapped to hear again.

Two rules decide what is stored, and they deliberately differ:

  * The session row needs >= MIN_READING_SECONDS of active playback — the PRD's
    minimum. Anything shorter is an accidental open, and logging it would drag
    down every WPM average the analytics page shows.
  * Word replays are stored whatever the duration. Tapping a word to hear it is
    never accidental, and it needs no playback at all: a reader can open a text
    and tap through its hard words without ever pressing Play. Holding replays
    to the 30-second rule would discard most of the signal the word bank (F49)
    is built from.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from backend.models import ReadingSession, User, WordRepeatLog
from backend.services.classifier_service import classify_batch, normalize_word

MIN_READING_SECONDS = 30
WORD_COLUMN_WIDTH = 100  # word_repeat_log.word and word_bank.word

# started_at comes from the client's clock and is only a label on the row, so a
# value outside this window is replaced with one derived from the server's.
_MAX_SESSION_AGE = timedelta(days=1)
_CLOCK_SKEW = timedelta(minutes=5)


@dataclass
class ReadingLogResult:
    session: ReadingSession | None
    # Every word whose count changed, mapped to its new all-time total. The word
    # bank's auto-population (F49) reads this to find words crossing its threshold.
    repeat_totals: dict[str, int] = field(default_factory=dict)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _session_start(started_at: datetime | None, duration_seconds: int, now: datetime) -> datetime:
    """The client's start time if it is plausible, else one derived from now."""
    fallback = now - timedelta(seconds=duration_seconds)
    if started_at is None:
        return fallback
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    started_at = started_at.astimezone(timezone.utc)
    if started_at > now + _CLOCK_SKEW or started_at < now - _MAX_SESSION_AGE:
        return fallback
    return started_at


def _clean_repeats(word_repeats: dict[str, int]) -> dict[str, int]:
    """Normalise words and merge the ones that collapse together.

    'Renewable' and 'renewable,' are one word to the reader and must share a
    row, so counts are summed after normalising rather than kept per spelling.
    Words that normalise to nothing or overflow the column are dropped rather
    than truncated into some other word.
    """
    cleaned: dict[str, int] = {}
    for raw, count in word_repeats.items():
        word = normalize_word(raw)
        if not word or len(word) > WORD_COLUMN_WIDTH or count <= 0:
            continue
        cleaned[word] = cleaned.get(word, 0) + count
    return cleaned


def log_reading_session(
    db: Session,
    user: User,
    *,
    started_at: datetime | None,
    duration_seconds: int,
    words_read: int,
    total_words: int,
    hard_word_count: int,
    source_type: str,
    simplified: bool,
    complexity_score: float | None,
    word_repeats: dict[str, int],
) -> ReadingLogResult:
    """Store one finished reading session and the words replayed during it."""
    now = _utcnow()
    repeats = _clean_repeats(word_repeats)
    result = ReadingLogResult(session=None)

    if duration_seconds >= MIN_READING_SECONDS:
        words_read = min(words_read, total_words)
        session = ReadingSession(
            user_id=user.id,
            date=_session_start(started_at, duration_seconds, now),
            # Words actually reached over active playback time. The client owns
            # the clock and has already excluded paused time.
            wpm=round(words_read / (duration_seconds / 60), 1),
            total_words=total_words,
            hard_word_count=min(hard_word_count, total_words),
            repeat_count=sum(repeats.values()),
            duration_seconds=duration_seconds,
            source_type=source_type,
            simplified=simplified,
            complexity_score=complexity_score,
        )
        db.add(session)
        result.session = session

    if repeats:
        # Labelled here rather than trusted from the client, so the log always
        # agrees with /classify — they are the same model.
        words = list(repeats)
        labels = {word: label for word, (label, _) in zip(words, classify_batch(words))}
        existing = {
            row.word: row
            for row in db.query(WordRepeatLog).filter(
                WordRepeatLog.user_id == user.id, WordRepeatLog.word.in_(words)
            )
        }
        for word, count in repeats.items():
            row = existing.get(word)
            if row is None:
                row = WordRepeatLog(user_id=user.id, word=word, repeat_count=0)
                db.add(row)
            row.repeat_count += count
            row.difficulty_label = labels[word]
            row.last_seen = now
            result.repeat_totals[word] = row.repeat_count

    if result.session is not None or repeats:
        db.commit()
        if result.session is not None:
            db.refresh(result.session)

    return result
