"""
F37/F38 — reading and writing session logging.

Checks the stored rows, not just what the endpoint reported, because the two
can disagree. Each test gets a throwaway account, so counts are exact rather
than measured as deltas.

The two rules that deliberately differ, and are the point of most of this file:
a session row needs at least 30 seconds of playback, while word replays are
stored whatever the duration — tapping a word is never accidental, and a reader
can work through a text's hard words without ever pressing Play.
"""

from datetime import datetime, timedelta, timezone

import pytest

from backend.models import ReadingSession, WordRepeatLog, WritingSession
from backend.tests.conftest import log_reading_session

BASE_SESSION = {
    "duration_seconds": 0,
    "words_read": 0,
    "total_words": 100,
    "hard_word_count": 5,
    "source_type": "paste",
    "simplified": False,
    "complexity_score": 8.2,
    "word_repeats": {},
}


def user_id(api, headers) -> str:
    return api.get("/auth/me", headers=headers).json()["id"]


# ── F37 · reading sessions ─────────────────────────────────────────────
def test_reading_requires_a_token(api):
    assert api.post("/sessions/reading", json=BASE_SESSION).status_code == 401


def test_short_session_with_no_replays_stores_nothing(api, new_user, db):
    _, headers = new_user("short")
    response = log_reading_session(api, headers, {}, duration_seconds=12)

    assert response.status_code == 200
    body = response.json()
    assert body["session_logged"] is False
    assert body["words_logged"] == 0
    assert db.query(ReadingSession).filter_by(user_id=user_id(api, headers)).count() == 0


def test_short_session_still_logs_its_replays(api, new_user, db):
    """A reader can tap through hard words without pressing Play at all."""
    _, headers = new_user("taps")
    uid = user_id(api, headers)

    body = log_reading_session(
        api,
        headers,
        # Two spellings of one word must merge; "..." normalises to nothing.
        {"Photolithographic,": 2, "photolithographic": 1, "...": 1},
        duration_seconds=5,
    ).json()

    assert body["session_logged"] is False
    assert body["words_logged"] == 1
    assert db.query(ReadingSession).filter_by(user_id=uid).count() == 0

    row = db.query(WordRepeatLog).filter_by(user_id=uid, word="photolithographic").one()
    assert row.repeat_count == 3, "the two spellings share one row"
    assert row.difficulty_label == "Hard", "labelled by the model, not the client"


def test_a_real_session_stores_its_fields_and_speed(api, new_user, db):
    _, headers = new_user("logged")
    started = datetime.now(timezone.utc) - timedelta(minutes=10)

    body = log_reading_session(
        api, headers, {"photolithographic": 1, "semiconductor": 2},
        duration_seconds=60,
        words_read=150,          # more than total_words: must be capped to it
        total_words=100,
        hard_word_count=5,
        complexity_score=8.2,
        started_at=started.isoformat(),
        source_type="pdf",
        simplified=True,
    ).json()

    assert body["session_logged"] is True and body["session_id"]
    row = db.get(ReadingSession, body["session_id"])
    assert row.wpm == 100.0, "words_read is capped at total_words: 100 words in 1 minute"
    assert row.repeat_count == 3, "replays during this session"
    assert (
        row.total_words, row.hard_word_count, row.duration_seconds,
        row.source_type, row.simplified, row.complexity_score,
    ) == (100, 5, 60, "pdf", True, 8.2)
    stored = row.date.replace(tzinfo=timezone.utc)
    assert abs((stored - started).total_seconds()) < 2, "a plausible client start time is kept"


def test_an_implausible_start_time_is_replaced(api, new_user, db):
    """started_at comes from the client's clock and is only a label on the row."""
    _, headers = new_user("skew")
    future = datetime.now(timezone.utc) + timedelta(days=3)

    body = log_reading_session(
        api, headers, {}, duration_seconds=45, words_read=30, started_at=future.isoformat()
    ).json()

    row = db.get(ReadingSession, body["session_id"])
    expected = datetime.now(timezone.utc) - timedelta(seconds=45)
    assert abs((row.date.replace(tzinfo=timezone.utc) - expected).total_seconds()) < 5
    assert row.wpm == 40.0, "30 words in 45 seconds"


def test_replays_accumulate_across_sessions(api, new_user, db):
    _, headers = new_user("accum")
    uid = user_id(api, headers)

    for _ in range(3):
        log_reading_session(api, headers, {"semiconductor": 2}, duration_seconds=5)

    row = db.query(WordRepeatLog).filter_by(user_id=uid, word="semiconductor").one()
    assert row.repeat_count == 6


@pytest.mark.parametrize(
    "name, payload",
    [
        ("unknown source_type", {"source_type": "email"}),
        ("negative duration", {"duration_seconds": -1}),
        ("a repeat count of zero", {"word_repeats": {"cat": 0}}),
        ("a repeat count over 100", {"word_repeats": {"cat": 101}}),
        ("501 distinct words", {"word_repeats": {f"w{i}": 1 for i in range(501)}}),
        ("an unexpected field", {"user_id": "someone-else"}),
    ],
)
def test_reading_payload_validation(api, new_user, name, payload):
    _, headers = new_user("valid")
    body = {**BASE_SESSION, **payload}
    assert api.post("/sessions/reading", headers=headers, json=body).status_code == 422, name


# ── F38 · writing sessions ─────────────────────────────────────────────
def test_writing_requires_a_token(api):
    assert api.post("/sessions/writing", json={"word_count": 5}).status_code == 401


def test_an_untouched_notepad_is_not_a_session(api, new_user):
    _, headers = new_user("empty")
    response = api.post("/sessions/writing", headers=headers, json={"word_count": 0})
    assert response.status_code == 400


def test_writing_session_upserts_onto_one_row(api, new_user, db):
    """Counts are absolute totals, so a repeated report settles rather than compounding."""
    _, headers = new_user("writing")

    first = api.post(
        "/sessions/writing", headers=headers,
        json={"word_count": 12, "spell_error_count": 2, "template_used": "essay"},
    ).json()
    assert first["id"]
    assert str(first["date"]).endswith(("Z", "+00:00")), "date is serialised as UTC"

    second = api.post(
        "/sessions/writing", headers=headers,
        json={"session_id": first["id"], "word_count": 20,
              "grammar_error_count": 1, "template_used": "essay"},
    ).json()
    assert second["id"] == first["id"]
    assert second["date"] == first["date"], "date stays the session's start"

    # F48's original path, kept registered on this handler and marked deprecated.
    alias = api.patch(
        "/writing/session", headers=headers,
        json={"session_id": first["id"], "word_count": 25, "template_used": None},
    )
    assert alias.status_code == 200 and alias.json()["id"] == first["id"]

    assert db.query(WritingSession).filter_by(id=first["id"]).count() == 1
    stored = db.get(WritingSession, first["id"])
    db.refresh(stored)
    assert (stored.word_count, stored.template_used) == (25, None), (
        "last write wins, and a cleared template means no template"
    )


def test_unknown_writing_session_is_a_404(api, new_user):
    _, headers = new_user("missing")
    response = api.post(
        "/sessions/writing", headers=headers,
        json={"session_id": "00000000-0000-0000-0000-000000000000", "word_count": 3},
    )
    assert response.status_code == 404


def test_another_users_writing_session_is_also_a_404(api, new_user):
    """Missing and someone else's must be indistinguishable."""
    _, owner = new_user("owner")
    mine = api.post("/sessions/writing", headers=owner, json={"word_count": 9}).json()

    _, stranger = new_user("stranger")
    response = api.post(
        "/sessions/writing", headers=stranger,
        json={"session_id": mine["id"], "word_count": 99},
    )
    assert response.status_code == 404
