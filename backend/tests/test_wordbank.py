"""
F49/F50 — the word bank and its spaced-repetition drill.

A word earns its place by being replayed three times; from then on SM-2 decides
when it comes back. The schedule arithmetic itself is covered in test_sm2.py —
here the questions are whether the threshold is applied correctly, whether
progress survives, and whether the queue and grading endpoints agree with the
stored rows.
"""

from datetime import date, timedelta

import pytest

from backend.models import WordBank
from backend.tests.conftest import letters, log_reading_session


def user_id(api, headers) -> str:
    return api.get("/auth/me", headers=headers).json()["id"]


# ── F49 · promotion ────────────────────────────────────────────────────
def test_a_word_joins_on_its_third_replay(api, new_user):
    _, headers = new_user("promote")

    first = log_reading_session(api, headers, {"quixotic": 2}).json()
    assert first["words_added_to_bank"] == [], "two replays are not enough"
    assert api.get("/wordbank", headers=headers).json() == []

    second = log_reading_session(api, headers, {"quixotic": 1}).json()
    assert second["words_added_to_bank"] == ["quixotic"]

    entry = api.get("/wordbank", headers=headers).json()[0]
    assert entry["word"] == "quixotic"
    assert (entry["sm2_ef"], entry["sm2_interval"], entry["sm2_repetitions"]) == (2.5, 1, 0)
    assert entry["next_review"] == date.today().isoformat()
    assert entry["due"] is True, "due the same day, so it reaches the next drill"
    assert entry["difficulty_label"] in {"Easy", "Medium", "Hard"}
    assert entry["mastered"] is False
    assert entry["total_drills"] == 0 and entry["last_quality"] is None


def test_a_banked_word_is_never_re_added_or_reset(api, new_user):
    """Re-adding would wipe the progress the reader has built on the word."""
    _, headers = new_user("findorskip")
    log_reading_session(api, headers, {"quixotic": 3})
    api.post("/wordbank/drill/result", headers=headers, json={"word": "quixotic", "quality": 5})

    again = log_reading_session(api, headers, {"quixotic": 5}).json()
    assert again["words_added_to_bank"] == []

    bank = api.get("/wordbank", headers=headers).json()
    assert len(bank) == 1
    assert bank[0]["sm2_repetitions"] == 1, "the earlier answer still counts"


def test_spellings_that_normalise_together_share_one_word(api, new_user):
    _, headers = new_user("normalise")
    promoted = log_reading_session(api, headers, {"Renewable": 2, "renewable,": 1}).json()
    assert promoted["words_added_to_bank"] == ["renewable"]


# ── F50 · the drill queue ──────────────────────────────────────────────
def test_the_drill_is_capped_at_twenty_words(api, new_user):
    _, headers = new_user("cap")
    log_reading_session(api, headers, {word: 3 for word in letters(25)})

    assert len(api.get("/wordbank", headers=headers).json()) == 25
    queue = api.get("/wordbank/drill", headers=headers).json()
    assert len(queue) == 20
    assert all(entry["due"] for entry in queue)


def test_the_most_overdue_words_come_first(api, new_user, db):
    """When the cap bites, the words furthest behind must not be the ones left out."""
    _, headers = new_user("overdue")
    log_reading_session(api, headers, {word: 3 for word in letters(25)})

    rows = (
        db.query(WordBank)
        .filter_by(user_id=user_id(api, headers))
        .order_by(WordBank.word)
        .all()
    )
    rows[10].next_review = date.today() - timedelta(days=9)
    rows[11].next_review = date.today() - timedelta(days=4)
    db.commit()
    expected = [rows[10].word, rows[11].word]

    queue = api.get("/wordbank/drill", headers=headers).json()
    assert [entry["word"] for entry in queue[:2]] == expected


# ── F50 · grading ──────────────────────────────────────────────────────
def test_consecutive_correct_answers_push_the_word_further_out(api, new_user):
    """AC-40, end to end: the ladder from test_sm2 must reach the stored row."""
    _, headers = new_user("ladder")
    log_reading_session(api, headers, {"quixotic": 3})

    intervals = []
    for attempt in range(3):
        result = api.post(
            "/wordbank/drill/result", headers=headers,
            json={"word": "quixotic", "quality": 4},
        ).json()
        intervals.append(result["sm2_interval"])
        assert result["total_drills"] == attempt + 1
        assert result["sm2_ef"] == 2.5, "grade 4 is the neutral one"

    assert intervals == [1, 6, 15]

    entry = api.get("/wordbank", headers=headers).json()[0]
    assert entry["next_review"] == (date.today() + timedelta(days=15)).isoformat()
    assert entry["due"] is False
    assert api.get("/wordbank/drill", headers=headers).json() == [], "it has left the queue"


def test_a_lapse_brings_the_word_back_tomorrow(api, new_user):
    _, headers = new_user("lapse")
    log_reading_session(api, headers, {"quixotic": 3})
    for _ in range(3):
        api.post("/wordbank/drill/result", headers=headers,
                 json={"word": "quixotic", "quality": 4})

    lapsed = api.post("/wordbank/drill/result", headers=headers,
                      json={"word": "quixotic", "quality": 1}).json()
    assert (lapsed["sm2_interval"], lapsed["sm2_repetitions"]) == (1, 0)
    assert lapsed["next_review"] == (date.today() + timedelta(days=1)).isoformat()


def test_a_word_can_be_graded_by_the_spelling_it_was_shown_as(api, new_user):
    """The stored word is normalised, so a client echoing it back must still match."""
    _, headers = new_user("echo")
    log_reading_session(api, headers, {"quixotic": 3})

    response = api.post("/wordbank/drill/result", headers=headers,
                        json={"word": "Quixotic,", "quality": 3})
    assert response.status_code == 200 and response.json()["word"] == "quixotic"


# ── validation and isolation ───────────────────────────────────────────
def test_grading_an_unbanked_word_is_a_404(api, new_user):
    _, headers = new_user("unknown")
    response = api.post("/wordbank/drill/result", headers=headers,
                        json={"word": "neverseen", "quality": 3})
    assert response.status_code == 404


@pytest.mark.parametrize("quality", [-1, 6, 99])
def test_a_grade_outside_zero_to_five_is_rejected(api, new_user, quality):
    _, headers = new_user("grade")
    log_reading_session(api, headers, {"quixotic": 3})
    response = api.post("/wordbank/drill/result", headers=headers,
                        json={"word": "quixotic", "quality": quality})
    assert response.status_code == 422


def test_another_readers_word_is_a_404(api, new_user):
    _, owner = new_user("owner")
    log_reading_session(api, owner, {"quixotic": 3})

    _, stranger = new_user("stranger")
    response = api.post("/wordbank/drill/result", headers=stranger,
                        json={"word": "quixotic", "quality": 3})
    assert response.status_code == 404
    assert api.get("/wordbank", headers=stranger).json() == []


@pytest.mark.parametrize("path", ["/wordbank", "/wordbank/drill"])
def test_the_bank_requires_a_token(api, path):
    assert api.get(path).status_code == 401


def test_grading_requires_a_token(api):
    response = api.post("/wordbank/drill/result", json={"word": "quixotic", "quality": 3})
    assert response.status_code == 401
