"""
F39-F41 — the analytics endpoints behind the charts.

Each test gets a throwaway account, so the expected numbers are exact rather
than deltas against whatever history a shared account had accumulated.

Rows are occasionally inserted straight through the `db` fixture: some states
cannot be reached through the API at all (a writing session with no words is
refused, by design), and building twenty sessions over HTTP to check a limit of
twenty is slow for no extra confidence.
"""

from datetime import datetime, timedelta, timezone

import pytest

from backend.models import ReadingSession, WordRepeatLog, WritingSession
from backend.tests.conftest import log_reading_session

ENDPOINTS = (
    "/analytics/summary",
    "/analytics/reading",
    "/analytics/writing",
    "/analytics/difficult-words",
)


def user_id(api, headers) -> str:
    return api.get("/auth/me", headers=headers).json()["id"]


@pytest.mark.parametrize("path", ENDPOINTS)
def test_every_endpoint_requires_a_token(api, path):
    assert api.get(path).status_code == 401


# ── the empty state ────────────────────────────────────────────────────
def test_a_new_reader_gets_zeroes_not_a_404(api, new_user):
    """"Nothing yet" is a normal state for this page, not an error."""
    _, headers = new_user("fresh")

    summary = api.get("/analytics/summary", headers=headers)
    assert summary.status_code == 200
    assert summary.json() == {
        "reading_sessions": 0,
        "writing_sessions": 0,
        "total_words_read": 0,
        "total_minutes_read": 0.0,
        "avg_wpm": None,      # null, not 0: a zero would plot as a real speed
        "best_wpm": None,
    }

    for path in ENDPOINTS[1:]:
        response = api.get(path, headers=headers)
        assert response.status_code == 200 and response.json() == []


# ── F39 · the summary ──────────────────────────────────────────────────
def test_average_speed_is_weighted_by_time_not_by_session(api, new_user):
    """A 1-minute sprint must not count as much as a 10-minute read.

    200 wpm for 1 minute and 100 wpm for 10 minutes is 1,200 words over 11
    minutes: 109.1 wpm. A plain mean of the two session speeds would claim 150.
    """
    _, headers = new_user("weighted")
    log_reading_session(api, headers, {}, duration_seconds=60, words_read=200, total_words=200)
    log_reading_session(api, headers, {}, duration_seconds=600, words_read=1000, total_words=1000)

    summary = api.get("/analytics/summary", headers=headers).json()
    assert summary["reading_sessions"] == 2
    assert summary["total_words_read"] == 1200
    assert summary["total_minutes_read"] == 11.0
    assert summary["avg_wpm"] == 109.1
    assert summary["avg_wpm"] != 150.0, "that would be the unweighted mean"
    assert summary["best_wpm"] == 200.0


def test_summary_counts_writing_sessions_too(api, new_user):
    _, headers = new_user("counts")
    api.post("/sessions/writing", headers=headers, json={"word_count": 40})
    assert api.get("/analytics/summary", headers=headers).json()["writing_sessions"] == 1


# ── F39 · the reading list ─────────────────────────────────────────────
def test_reading_history_is_newest_first_and_capped_at_twenty(api, new_user, db):
    _, headers = new_user("history")
    uid = user_id(api, headers)

    now = datetime.now(timezone.utc)
    for index in range(25):
        db.add(ReadingSession(
            user_id=uid, date=now - timedelta(days=index), wpm=100 + index,
            total_words=100, hard_word_count=1, repeat_count=0,
            duration_seconds=60, source_type="paste", simplified=False,
            complexity_score=8.0,
        ))
    db.commit()

    rows = api.get("/analytics/reading", headers=headers).json()
    assert len(rows) == 20, "the chart shows the last twenty"
    dates = [row["date"] for row in rows]
    assert dates == sorted(dates, reverse=True)
    assert rows[0]["wpm"] == 100.0, "the newest session is the one with no offset"


def test_words_read_is_recovered_from_speed_and_duration(api, new_user):
    """Not a stored column: wpm x minutes gives it back exactly."""
    _, headers = new_user("wordsread")
    log_reading_session(api, headers, {}, duration_seconds=36, words_read=82, total_words=82)

    row = api.get("/analytics/reading", headers=headers).json()[0]
    assert row["duration_seconds"] == 36
    assert row["words_read"] == 82


def test_timestamps_carry_a_utc_offset(api, new_user):
    """SQLite returns naive datetimes; without a marker a browser reads local time."""
    _, headers = new_user("utc")
    log_reading_session(api, headers, {}, duration_seconds=40, words_read=40, total_words=40)
    api.post("/sessions/writing", headers=headers, json={"word_count": 10})

    for path in ("/analytics/reading", "/analytics/writing"):
        for row in api.get(path, headers=headers).json():
            assert row["date"].endswith(("Z", "+00:00")), path


# ── F40 · writing errors ───────────────────────────────────────────────
def test_error_rate_is_errors_per_hundred_words(api, new_user):
    _, headers = new_user("rate")
    api.post("/sessions/writing", headers=headers, json={
        "word_count": 200, "spell_error_count": 3,
        "grammar_error_count": 2, "homophone_flag_count": 1,
    })

    row = api.get("/analytics/writing", headers=headers).json()[0]
    assert row["error_rate"] == 3.0, "6 errors in 200 words"


def test_a_session_with_no_words_has_no_rate(api, new_user, db):
    """Inserted directly: the endpoint refuses a zero-word session, rightly.

    The chart leaves these out rather than plotting them as a perfect zero,
    and the table shows a dash.
    """
    _, headers = new_user("norate")
    db.add(WritingSession(user_id=user_id(api, headers), word_count=0))
    db.commit()

    row = api.get("/analytics/writing", headers=headers).json()[0]
    assert row["error_rate"] is None


# ── F41 · difficult words ──────────────────────────────────────────────
def test_difficult_words_are_the_ten_most_replayed(api, new_user, db):
    _, headers = new_user("difficult")
    uid = user_id(api, headers)

    now = datetime.now(timezone.utc)
    for index in range(14):
        db.add(WordRepeatLog(
            user_id=uid, word=f"zzword{chr(97 + index)}", repeat_count=index + 1,
            difficulty_label="Hard", last_seen=now,
        ))
    db.commit()

    rows = api.get("/analytics/difficult-words", headers=headers).json()
    assert len(rows) == 10
    counts = [row["repeat_count"] for row in rows]
    assert counts == sorted(counts, reverse=True)
    assert counts[0] == 14, "the most-replayed word leads"
    assert rows[0]["difficulty_label"] == "Hard"
    assert rows[0]["last_seen"].endswith(("Z", "+00:00"))


def test_ties_prefer_the_word_replayed_most_recently(api, new_user, db):
    """A word being struggled with now beats one settled long ago."""
    _, headers = new_user("ties")
    uid = user_id(api, headers)
    now = datetime.now(timezone.utc)

    db.add(WordRepeatLog(user_id=uid, word="zzstale", repeat_count=5,
                         difficulty_label="Hard", last_seen=now - timedelta(days=30)))
    db.add(WordRepeatLog(user_id=uid, word="zzrecent", repeat_count=5,
                         difficulty_label="Hard", last_seen=now))
    db.commit()

    words = [row["word"] for row in api.get("/analytics/difficult-words", headers=headers).json()]
    assert words[:2] == ["zzrecent", "zzstale"]


# ── isolation ──────────────────────────────────────────────────────────
def test_one_readers_analytics_never_include_anothers(api, new_user):
    _, mine = new_user("mine")
    log_reading_session(api, mine, {"semiconductor": 4}, duration_seconds=60,
                        words_read=100, total_words=100)

    _, theirs = new_user("theirs")
    assert api.get("/analytics/summary", headers=theirs).json()["reading_sessions"] == 0
    assert api.get("/analytics/reading", headers=theirs).json() == []
    assert api.get("/analytics/difficult-words", headers=theirs).json() == []
