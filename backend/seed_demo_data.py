"""
backend/seed_demo_data.py
Fill a development database with believable M3 history, for reviewing the
analytics and word-bank features without using the app for a month.

    backend/venv/Scripts/python.exe backend/seed_demo_data.py

Not imported by the application. It writes rows directly rather than going
through the API, because the point is history: a session dated three weeks ago
cannot be created by POSTing to /sessions/reading, which timestamps from the
server's clock.

Safe to run repeatedly. It owns exactly one account — the demo user — and
clears that user's M3 rows before reseeding, so the result is the same every
time and no other account is touched. The data is generated from a fixed seed,
so two runs produce identical numbers.

It refuses to run against anything but SQLite: the PRD deploys on PostgreSQL,
and a seeding script pointed at a real database would be a bad afternoon.
"""

from __future__ import annotations

import argparse
import random
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from pydantic import BaseModel, EmailStr, ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.database import DATABASE_URL, SessionLocal, init_db
from backend.models import (
    DrillDay,
    ReadingSession,
    User,
    WordBank,
    WordRepeatLog,
    WritingSession,
)
from backend.services.auth_service import hash_password
from backend.services.classifier_service import classify_batch
from backend.services.sm2_service import MASTERED_EF, MASTERED_INTERVAL
from backend.services.wordbank_service import PROMOTION_THRESHOLD

# example.com, not a .test domain: the API validates addresses with pydantic's
# EmailStr, which refuses reserved names like .test and .local. Seeding writes
# rows directly and so skips that check - an account created with one of those
# addresses exists in the table but can never log in.
DEMO_EMAIL = "demo@example.com"
DEMO_PASSWORD = "DemoPassw0rd!23"
DEMO_NAME = "Demo Reader"

SEED = 20260912

# Words a struggling reader might replay, with how often. Everything at or
# above PROMOTION_THRESHOLD ends up in the word bank, which is what makes the
# F41 chart and the F49 bank agree with each other.
REPLAYED_WORDS = {
    "photolithographic": 9,
    "semiconductor": 7,
    "encyclopedia": 6,
    "photosynthesis": 5,
    "extraordinarily": 5,
    "fabrication": 4,
    "consultation": 4,
    "renewable": 3,
    "innovations": 3,
    "specialised": 3,
    # Not every replayed word is a hard one - readers ask for ordinary words
    # too, and these give a reviewer all three difficulty chips to look at.
    # The labels are the model's, not chosen here: "community" and "garden"
    # come out Medium, "school" Easy.
    "community": 4,
    "garden": 3,
    "school": 3,
    "pollution": 2,   # below the threshold on purpose: in the chart, not the bank
    "ordinary": 1,
}

SOURCES = ("paste", "pdf", "image", "sample", "paste", "pdf")


def utc(days_ago: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days_ago)


def clear_demo_rows(db, user_id: str) -> dict[str, int]:
    """Remove only this user's M3 history, so a reseed is not cumulative."""
    counts = {}
    for model in (ReadingSession, WritingSession, WordRepeatLog, WordBank, DrillDay):
        counts[model.__tablename__] = (
            db.query(model).filter(model.user_id == user_id).delete()
        )
    db.commit()
    return counts


def seed_reading_sessions(db, user_id: str, rng: random.Random) -> int:
    """Eighteen sessions over four weeks, with speed improving.

    Two sessions land on the same day on purpose: the WPM chart labels each day
    once, and that path only runs when a day repeats.
    """
    day_offsets = [27, 26, 24, 21, 20, 20, 18, 15, 14, 12, 11, 9, 7, 6, 4, 3, 1, 0.2]
    for index, days_ago in enumerate(day_offsets):
        progress = index / (len(day_offsets) - 1)          # 0 -> 1 across the month
        target_wpm = 85 + progress * 45 + rng.uniform(-8, 8)
        duration = rng.choice([120, 180, 240, 300, 420, 600, 780])
        words_read = max(20, round(target_wpm * duration / 60))
        total_words = words_read + rng.randint(0, 60)

        db.add(ReadingSession(
            user_id=user_id,
            date=utc(days_ago),
            # The same formula session_service uses, so analytics can recover
            # words_read as wpm x minutes.
            wpm=round(words_read / (duration / 60), 1),
            total_words=total_words,
            hard_word_count=round(total_words * rng.uniform(0.06, 0.18)),
            repeat_count=rng.randint(0, 6),
            duration_seconds=duration,
            source_type=SOURCES[index % len(SOURCES)],
            simplified=rng.random() < 0.35,
            complexity_score=round(rng.uniform(6.5, 14.0), 1),
        ))
    db.commit()
    return len(day_offsets)


def seed_writing_sessions(db, user_id: str, rng: random.Random) -> int:
    """Nine sessions with real error counts, improving over three weeks.

    Non-zero counts are the point. Every writing session logged during this
    project's Smart App Control outages stored zeroes, so the F40 chart has
    only ever been reviewed flat.
    """
    day_offsets = [20, 17, 15, 12, 10, 7, 4, 1]
    templates = ("essay", None, "email", "essay", "report", None, "essay", "report")

    for index, days_ago in enumerate(day_offsets):
        progress = index / (len(day_offsets) - 1)
        word_count = rng.randint(140, 620)
        # Errors per 100 words falling from roughly 5 to roughly 1.
        rate = 5.0 - progress * 4.0 + rng.uniform(-0.4, 0.4)
        errors = max(1, round(rate * word_count / 100))
        spelling = round(errors * 0.55)
        grammar = round(errors * 0.3)
        db.add(WritingSession(
            user_id=user_id,
            date=utc(days_ago),
            word_count=word_count,
            spell_error_count=spelling,
            grammar_error_count=grammar,
            homophone_flag_count=max(0, errors - spelling - grammar),
            template_used=templates[index],
        ))

    # One session that was opened and left. Its error rate is null rather than
    # zero, so the chart skips it and the table shows a dash - worth having in
    # the sample, since that path is easy to get wrong.
    db.add(WritingSession(user_id=user_id, date=utc(9), word_count=0))
    db.commit()
    return len(day_offsets) + 1


def seed_words(db, user_id: str) -> tuple[int, int]:
    """The replay log, and the word bank it feeds.

    Labels come from the real classifier, so the bank agrees with /classify
    rather than carrying invented difficulty labels.
    """
    words = list(REPLAYED_WORDS)
    labels = {word: label for word, (label, _) in zip(words, classify_batch(words))}

    for index, (word, count) in enumerate(REPLAYED_WORDS.items()):
        db.add(WordRepeatLog(
            user_id=user_id,
            word=word,
            repeat_count=count,
            difficulty_label=labels[word],
            last_seen=utc(index * 1.5),
        ))

    # A spread of schedules, so the drill has something due, the stats card has
    # something mastered, and the bank shows words at every stage.
    #            word                     ef   interval  reps  due in  drills  last
    schedules = [
        ("photolithographic",            1.9,        1,    0,      0,      6,    1),
        ("semiconductor",                2.5,        1,    0,      0,      0, None),
        ("encyclopedia",                 2.4,        6,    2,      0,      3,    3),
        ("photosynthesis",               2.6,        6,    2,      0,      2,    4),
        ("extraordinarily",              2.5,        1,    1,      1,      1,    4),
        ("fabrication",                  2.7,       15,    3,      4,      4,    5),
        ("consultation",                 2.5,       15,    3,      9,      3,    4),
        ("renewable",   MASTERED_EF + 0.2, MASTERED_INTERVAL + 9,  6,     24,      7,    5),
        ("innovations", MASTERED_EF,      MASTERED_INTERVAL,       5,     17,      6,    5),
        ("specialised",                  2.3,        6,    2,      2,      2,    3),
        ("community",                    2.8,        6,    2,      0,      2,    5),
        ("garden",                       2.9,       15,    3,      6,      3,    5),
        ("school",                       2.6,        1,    1,      0,      1,    4),
    ]
    # Every word at or above the threshold must be in the bank, or the sample
    # would contradict F49's own rule.
    assert {word for word, count in REPLAYED_WORDS.items() if count >= PROMOTION_THRESHOLD} == {
        row[0] for row in schedules
    }
    today = date.today()
    for word, ef, interval, reps, due_in, drills, quality in schedules:
        assert REPLAYED_WORDS[word] >= PROMOTION_THRESHOLD, word
        db.add(WordBank(
            user_id=user_id,
            word=word,
            difficulty_label=labels[word],
            sm2_ef=ef,
            sm2_interval=interval,
            sm2_repetitions=reps,
            next_review=today + timedelta(days=due_in),
            total_drills=drills,
            last_quality=quality,
            added_at=utc(20),
        ))

    # Five consecutive days of practice, ending today, so the streak is real.
    for days_ago in range(5):
        db.add(DrillDay(
            user_id=user_id,
            day=today - timedelta(days=days_ago),
            words_drilled=[4, 3, 6, 2, 5][days_ago],
        ))

    db.commit()
    return len(REPLAYED_WORDS), len(schedules)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", default=DEMO_EMAIL, help="the demo account to fill")
    parser.add_argument("--password", default=DEMO_PASSWORD)
    args = parser.parse_args()

    if not DATABASE_URL.startswith("sqlite"):
        raise SystemExit(
            f"refusing to seed {DATABASE_URL}: this writes demo rows and is for "
            "development databases only."
        )

    # The same validation the API applies, run here because writing rows
    # directly skips it. Without this, an address the API refuses produces an
    # account that exists but cannot log in - which is the one thing this
    # script has to get right.
    class _Address(BaseModel):
        email: EmailStr

    try:
        _Address(email=args.email)
    except ValidationError as exc:
        raise SystemExit(
            f"{args.email} is not an address this API accepts, so the seeded "
            f"account could not log in:\n  {exc.errors()[0]['msg']}"
        ) from exc

    print(f"Database: {DATABASE_URL}")

    init_db()
    rng = random.Random(SEED)

    with SessionLocal() as db:
        user = db.query(User).filter_by(email=args.email).one_or_none()
        if user is None:
            user = User(
                name=DEMO_NAME,
                email=args.email,
                password_hash=hash_password(args.password),
            )
            db.add(user)
            db.commit()
            db.refresh(user)
            print(f"Created {args.email}")
        else:
            # An existing demo account keeps working: the password is reset so
            # the credentials printed below are always the ones that work.
            user.password_hash = hash_password(args.password)
            db.commit()
            removed = clear_demo_rows(db, user.id)
            summary = ", ".join(
                f"{count} {table}" for table, count in removed.items() if count
            )
            print(f"Reusing {args.email}; cleared {summary or 'nothing'}")

        reading = seed_reading_sessions(db, user.id, rng)
        writing = seed_writing_sessions(db, user.id, rng)
        replayed, banked = seed_words(db, user.id)

        due_today = (
            db.query(WordBank)
            .filter(WordBank.user_id == user.id, WordBank.next_review <= date.today())
            .count()
        )
        mastered = (
            db.query(WordBank)
            .filter(
                WordBank.user_id == user.id,
                WordBank.sm2_ef >= MASTERED_EF,
                WordBank.sm2_interval >= MASTERED_INTERVAL,
            )
            .count()
        )

    print(
        f"\nSeeded: {reading} reading sessions, {writing} writing sessions "
        f"(one with no words), {replayed} replayed words, {banked} in the word bank."
    )
    print(f"\nLog in as  {args.email}  /  {args.password}")
    print("\nWhat to expect:")
    print("  Analytics  speed trend rising over four weeks; error rate falling;")
    print("             top-10 replayed words, tallest is photolithographic")
    print(f"  Word bank  {banked} words, {due_today} due today, {mastered} mastered, 5-day streak")
    print("  Home       a reminder card; the nav badge shows the due count")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
