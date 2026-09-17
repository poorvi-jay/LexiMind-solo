"""The four end-to-end journeys, automated (M4 phase 6).

Every route these touch already has its own tests. What none of those cover is
whether the routes *connect* — whether the output of one step is actually
usable as the input to the next. That is where this kind of app breaks: each
endpoint stays correct in isolation while the seam between two of them quietly
stops lining up, and the only thing that notices is a person clicking through.

These four journeys were verified by hand during M3. This file is that walk-
through, written down:

    Reading    upload a PDF -> classify -> syllabify -> define -> log -> it shows
    Writing    type -> check -> predict -> save -> log -> it shows
    Analytics  empty -> log -> counted -> and invisible to anyone else
    Word bank  replay 3x -> banked -> due -> graded -> rescheduled

Each test is deliberately one long function rather than several small ones. A
journey split across tests stops being a journey: the assertion that matters
is that step 5 works *on the output of step 4*, not on a fixture that looks
like it.
"""

from __future__ import annotations

from datetime import date

import pytest

from backend.tests.conftest import log_reading_session, sample_pdf

MESSY_DRAFT = "Their going too the libary tomorow to reserch there projekt."


@pytest.fixture
def reader(api, new_user):
    _, headers = new_user("flow")
    return headers


# ── flow 1: reading ────────────────────────────────────────────────────

def test_reading_journey_from_upload_to_logged_session(api, reader):
    """A reader opens a PDF, works through it, and the session is recorded."""

    # 1. Upload. The text layer comes back page by page.
    uploaded = api.post(
        "/ocr/pdf",
        headers=reader,
        files={"file": ("notes.pdf", sample_pdf("Photosynthesis converts light energy"),
                        "application/pdf")},
    )
    assert uploaded.status_code == 200, uploaded.text[:200]
    text = uploaded.json()["text"]
    assert "Photosynthesis" in text

    # 2. Classify the words the page actually holds — not a fixture list.
    # This is the seam AC-28 cares about: the page classifies the document's
    # own tokens, once, in a batch.
    tokens = text.split()
    classified = api.post("/classify", headers=reader, json={"words": tokens})
    assert classified.status_code == 200
    results = classified.json()["results"]

    assert [r["word"] for r in results] == tokens, (
        "the echo contract broke mid-journey: highlighting indexes on this"
    )
    hard_words = [r["word"] for r in results if r["label"] == "Hard"]
    assert hard_words, f"nothing classified Hard in {tokens}"

    # 3. Syllabify what was flagged. The page does this for the whole page at
    # once, keyed on the cleaned word.
    syllabified = api.post(
        "/reading/syllabify", headers=reader, json={"words": hard_words}
    )
    assert syllabified.status_code == 200
    assert syllabified.json()["results"], "no syllable breakdowns for the hard words"

    # 4. Tap one for its definition.
    target = hard_words[0].strip(".,").lower()
    defined = api.post("/reading/define", headers=reader, json={"word": target})
    assert defined.status_code == 200
    assert defined.json()["definition"], f"no definition for {target!r}"

    # 5. Finish reading, replaying that word three times on the way.
    logged = log_reading_session(
        api,
        reader,
        {target: 3},
        duration_seconds=60,
        words_read=len(tokens),
        total_words=len(tokens),
        hard_word_count=len(hard_words),
        source_type="pdf",
    )
    assert logged.status_code == 200
    session = logged.json()
    assert session["session_logged"] is True
    assert session["wpm"] and session["wpm"] > 0
    assert session["words_logged"] >= 1

    # 6. It shows up where the reader would look for it.
    series = api.get("/analytics/reading", headers=reader)
    assert series.status_code == 200
    assert any(point["id"] == session["session_id"] for point in series.json()), (
        "the session was accepted but does not appear in the reading series"
    )


# ── flow 2: writing ────────────────────────────────────────────────────

def test_writing_journey_from_draft_to_logged_session(api, reader):
    """Someone writes badly, gets help, saves, and the session records it."""

    # 1. Typing autosaves, which creates the document.
    draft = api.patch(
        "/writing/autosave", headers=reader, json={"content": MESSY_DRAFT}
    )
    assert draft.status_code == 200
    document_id = draft.json()["id"]

    # 2. The checks run on what was typed.
    checked = api.post("/nlp/check", headers=reader, json={"text": MESSY_DRAFT})
    assert checked.status_code == 200
    issues = checked.json()
    assert issues["issues"], "no issues in a deliberately broken sentence"

    # Offsets have to index the submitted text, or the editor underlines the
    # wrong words — the seam between the checker and the editor.
    for issue in issues["issues"]:
        assert MESSY_DRAFT[issue["start"]:issue["end"]] == issue["text"]

    # 3. Prediction offers a continuation.
    predicted = api.post(
        "/nlp/predict", headers=reader, json={"text": "They are going to the"}
    )
    assert predicted.status_code == 200
    assert isinstance(predicted.json()["words"], list)

    # 4. 'Save as' keeps it under a name.
    saved = api.post(
        "/writing/documents",
        headers=reader,
        json={"title": "Project notes", "content": MESSY_DRAFT, "template": "essay"},
    )
    assert saved.status_code == 201

    # 5. Report the session using the counts the checker actually produced —
    # AC-30 wants this exercised with non-zero errors, not an empty document.
    counts = issues["counts"]
    reported = api.post(
        "/sessions/writing",
        headers=reader,
        json={
            "word_count": len(MESSY_DRAFT.split()),
            "spell_error_count": counts.get("spelling", 0),
            "grammar_error_count": counts.get("grammar", 0),
            "homophone_flag_count": counts.get("homophone", 0),
            "template_used": "essay",
        },
    )
    assert reported.status_code == 200
    assert reported.json()["spell_error_count"] == counts.get("spelling", 0)

    # 6. And it is visible in the analytics.
    series = api.get("/analytics/writing", headers=reader)
    assert series.status_code == 200
    assert any(point["id"] == reported.json()["id"] for point in series.json())

    # The document survived all of that.
    assert api.get(f"/writing/documents/{document_id}", headers=reader).status_code == 200


# ── flow 3: analytics ──────────────────────────────────────────────────

def test_analytics_journey_from_empty_to_populated_and_still_private(
    api, reader, new_user
):
    """The numbers start empty, count what happened, and stay private."""

    # 1. A new account has nothing. avg_wpm is None rather than 0 — a zero
    # would plot as a real reading speed of zero words per minute.
    empty = api.get("/analytics/summary", headers=reader).json()
    assert empty["reading_sessions"] == 0
    assert empty["writing_sessions"] == 0
    assert empty["avg_wpm"] is None, "an empty account reports a speed"

    # 2. Do some of each.
    log_reading_session(
        api, reader, {}, duration_seconds=60, words_read=200, total_words=200
    )
    api.post(
        "/sessions/writing",
        headers=reader,
        json={"word_count": 120, "spell_error_count": 3},
    )

    # 3. Both are counted.
    summary = api.get("/analytics/summary", headers=reader).json()
    assert summary["reading_sessions"] == 1
    assert summary["writing_sessions"] == 1
    assert summary["avg_wpm"] == pytest.approx(200, rel=0.05), (
        f"200 words in 60s should be ~200 wpm, got {summary['avg_wpm']}"
    )
    assert summary["best_wpm"] == summary["avg_wpm"], "one session, so they match"

    # 4. None of it reaches anyone else. Isolation is asserted here as well as
    # in test_isolation.py because this is where it would actually bite: the
    # summary aggregates, and an aggregate that forgets its WHERE clause still
    # returns plausible-looking numbers.
    _, stranger = new_user("stranger")
    theirs = api.get("/analytics/summary", headers=stranger).json()
    assert theirs["reading_sessions"] == 0
    assert theirs["avg_wpm"] is None


# ── flow 4: word bank ──────────────────────────────────────────────────

def test_word_bank_journey_from_replay_to_reschedule(api, reader):
    """A replayed word is banked, comes up for practice, and is rescheduled."""
    word = "semiconductor"

    # 1. Three replays in a session is the promotion threshold (F49).
    logged = log_reading_session(api, reader, {word: 3}, duration_seconds=60)
    assert logged.status_code == 200
    assert word in logged.json()["words_added_to_bank"], (
        f"three replays did not bank {word!r}: "
        f"{logged.json()['words_added_to_bank']}"
    )

    # 2. It is in the bank, with SM-2 in its starting state.
    bank = api.get("/wordbank", headers=reader).json()
    entry = next(e for e in bank if e["word"] == word)
    assert entry["sm2_repetitions"] == 0
    assert entry["total_drills"] == 0

    # 3. And it is offered for practice today.
    due = api.get("/wordbank/drill", headers=reader).json()
    assert word in [item["word"] for item in due], "a new word is not due"

    stats_before = api.get("/wordbank/stats", headers=reader).json()
    assert stats_before["total_in_bank"] >= 1
    assert stats_before["words_due_today"] >= 1

    # 4. Answer it well.
    graded = api.post(
        "/wordbank/drill/result", headers=reader, json={"word": word, "quality": 5}
    )
    assert graded.status_code == 200
    result = graded.json()

    assert result["sm2_repetitions"] == 1
    assert result["total_drills"] == 1
    assert result["last_quality"] == 5
    assert result["sm2_ef"] > 2.5, "a perfect answer should raise the ease factor"
    assert date.fromisoformat(result["next_review"]) > date.today(), (
        "a correctly answered word is still due today, so the queue never drains"
    )

    # 5. So it stops being due, which is the whole point of the schedule.
    still_due = [item["word"] for item in api.get("/wordbank/drill", headers=reader).json()]
    assert word not in still_due, "a just-practised word came straight back"

    # 6. The stats agree: still banked, no longer due.
    stats_after = api.get("/wordbank/stats", headers=reader).json()
    assert stats_after["total_in_bank"] == stats_before["total_in_bank"]
    assert stats_after["words_due_today"] == stats_before["words_due_today"] - 1
    assert stats_after["streak"] >= 1, "practising today did not start a streak"
