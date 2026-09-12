"""
F51 — the word bank's stats, the practice streak, and drill-due dates.

The streak and the date-boundary cases cannot be reached through the API alone,
so past drill days and schedules are written through the `db` fixture. That is
the point of these tests: they check behaviour a day or a fortnight from now
without waiting for it.
"""

from datetime import date, timedelta

from backend.models import DrillDay, WordBank
from backend.tests.conftest import letters, log_reading_session

TODAY = date.today()


def user_id(api, headers) -> str:
    return api.get("/auth/me", headers=headers).json()["id"]


def set_drill_days(db, uid, days):
    """Replace this reader's practice history with exactly `days`."""
    db.query(DrillDay).filter_by(user_id=uid).delete()
    for day in days:
        db.add(DrillDay(user_id=uid, day=day, words_drilled=1))
    db.commit()


def test_requires_a_token(api):
    assert api.get("/wordbank/stats").status_code == 401


def test_an_empty_bank_reports_zeroes(api, new_user):
    _, headers = new_user("emptystats")
    assert api.get("/wordbank/stats", headers=headers).json() == {
        "words_due_today": 0,
        "due_words": [],
        "total_in_bank": 0,
        "mastered": 0,
        "streak": 0,
    }


def test_due_words_is_a_preview_of_the_full_count(api, new_user):
    """The badge shows everything waiting; the card lists only the first few."""
    _, headers = new_user("preview")
    log_reading_session(api, headers, {word: 3 for word in letters(7)})

    stats = api.get("/wordbank/stats", headers=headers).json()
    assert stats["words_due_today"] == 7
    assert stats["due_words"] == ["zzaa", "zzbb", "zzcc", "zzdd", "zzee"]
    assert stats["total_in_bank"] == 7
    assert stats["mastered"] == 0
    assert stats["streak"] == 0, "banking words is not practising them"


def test_grading_clears_a_word_from_the_due_counts(api, new_user):
    _, headers = new_user("clears")
    log_reading_session(api, headers, {word: 3 for word in letters(3)})

    api.post("/wordbank/drill/result", headers=headers, json={"word": "zzaa", "quality": 4})

    stats = api.get("/wordbank/stats", headers=headers).json()
    assert stats["words_due_today"] == 2
    assert "zzaa" not in stats["due_words"]
    assert stats["total_in_bank"] == 3, "it is still in the bank, just not due"
    assert stats["streak"] == 1, "a drill today starts the streak"


def test_mastery_needs_both_thresholds(api, new_user, db):
    _, headers = new_user("mastery")
    log_reading_session(api, headers, {word: 3 for word in letters(4)})

    rows = (
        db.query(WordBank)
        .filter_by(user_id=user_id(api, headers))
        .order_by(WordBank.word)
        .all()
    )
    rows[0].sm2_ef, rows[0].sm2_interval = 2.5, 21   # exactly at both
    rows[1].sm2_ef, rows[1].sm2_interval = 2.4, 21   # ef just below
    rows[2].sm2_ef, rows[2].sm2_interval = 2.6, 20   # interval just below
    db.commit()

    assert api.get("/wordbank/stats", headers=headers).json()["mastered"] == 1


# ── the streak ─────────────────────────────────────────────────────────
def test_consecutive_days_ending_today(api, new_user, db):
    _, headers = new_user("streak3")
    uid = user_id(api, headers)
    set_drill_days(db, uid, [TODAY, TODAY - timedelta(days=1), TODAY - timedelta(days=2)])

    assert api.get("/wordbank/stats", headers=headers).json()["streak"] == 3


def test_a_streak_survives_a_day_not_yet_practised(api, new_user, db):
    """Counting only from today would show 0 every morning, which reads as a
    lost streak rather than a day not started."""
    _, headers = new_user("yesterday")
    uid = user_id(api, headers)
    set_drill_days(db, uid, [TODAY - timedelta(days=1), TODAY - timedelta(days=2),
                             TODAY - timedelta(days=3)])

    assert api.get("/wordbank/stats", headers=headers).json()["streak"] == 3


def test_a_gap_ends_the_streak(api, new_user, db):
    _, headers = new_user("gap")
    uid = user_id(api, headers)
    set_drill_days(db, uid, [TODAY, TODAY - timedelta(days=1), TODAY - timedelta(days=3)])

    assert api.get("/wordbank/stats", headers=headers).json()["streak"] == 2


def test_two_idle_days_reset_the_streak(api, new_user, db):
    _, headers = new_user("idle")
    uid = user_id(api, headers)
    set_drill_days(db, uid, [TODAY - timedelta(days=2), TODAY - timedelta(days=3)])

    assert api.get("/wordbank/stats", headers=headers).json()["streak"] == 0


def test_grading_records_one_day_however_many_words(api, new_user, db):
    _, headers = new_user("oneday")
    log_reading_session(api, headers, {word: 3 for word in letters(3)})
    for word in letters(3):
        api.post("/wordbank/drill/result", headers=headers, json={"word": word, "quality": 4})

    rows = db.query(DrillDay).filter_by(user_id=user_id(api, headers)).all()
    assert len(rows) == 1, "one row per day, upserted"
    assert rows[0].words_drilled == 3
    assert api.get("/wordbank/stats", headers=headers).json()["streak"] == 1


# ── drill-due across a date boundary ───────────────────────────────────
def test_due_today_counts_but_due_tomorrow_does_not(api, new_user, db):
    _, headers = new_user("boundary")
    log_reading_session(api, headers, {"quixotic": 3})
    uid = user_id(api, headers)

    def set_review(day):
        db.query(WordBank).filter_by(user_id=uid).one().next_review = day
        db.commit()

    set_review(TODAY)
    assert api.get("/wordbank/stats", headers=headers).json()["words_due_today"] == 1
    assert [e["word"] for e in api.get("/wordbank/drill", headers=headers).json()] == ["quixotic"]

    set_review(TODAY + timedelta(days=1))
    assert api.get("/wordbank/stats", headers=headers).json()["words_due_today"] == 0
    assert api.get("/wordbank/drill", headers=headers).json() == []

    set_review(TODAY - timedelta(days=1))
    assert api.get("/wordbank/stats", headers=headers).json()["words_due_today"] == 1, (
        "an overdue word is still due"
    )


def test_stats_never_include_another_readers_words(api, new_user):
    _, owner = new_user("owner")
    log_reading_session(api, owner, {word: 3 for word in letters(3)})

    _, stranger = new_user("stranger")
    stats = api.get("/wordbank/stats", headers=stranger).json()
    assert stats["total_in_bank"] == 0 and stats["words_due_today"] == 0
