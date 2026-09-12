"""
backend/services/wordbank_service.py
F49 — filling the personal word bank from what a reader actually replays.

A word earns its place by being asked for repeatedly: once its all-time replay
count reaches PROMOTION_THRESHOLD it joins the bank with a fresh SM-2 schedule,
due the same day. Nothing else adds words, and nothing removes them.

This runs inside the session-logging transaction (see session_service), so a
session and the words it promotes are committed together — a reader never sees
a session logged without the word bank that session earned.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from backend.models import User, WordBank

# The PRD's rule: three replays of the same word, counted across every session.
PROMOTION_THRESHOLD = 3


def sync_word_bank(
    db: Session,
    user: User,
    repeat_totals: dict[str, int],
    labels: dict[str, str] | None = None,
) -> list[str]:
    """Promote newly-qualifying words. Returns the words added, in order.

    `repeat_totals` maps word -> all-time replay count, as returned by
    session_service. Words already in the bank are left on their schedule and
    only have their difficulty label refreshed: re-adding one would reset the
    reader's progress on the word they have been practising.

    Adds to the session but does not commit; the caller owns the transaction.
    """
    labels = labels or {}
    qualifying = [
        word for word, total in repeat_totals.items() if total >= PROMOTION_THRESHOLD
    ]
    if not qualifying:
        return []

    # Find-or-skip rather than a blind insert: word is unique per user, and a
    # word can cross the threshold in one session and be replayed again in the
    # next.
    existing = {
        row.word: row
        for row in db.query(WordBank).filter(
            WordBank.user_id == user.id, WordBank.word.in_(qualifying)
        )
    }

    today = date.today()
    added: list[str] = []
    for word in qualifying:
        row = existing.get(word)
        if row is not None:
            if labels.get(word):
                row.difficulty_label = labels[word]
            continue
        db.add(
            WordBank(
                user_id=user.id,
                word=word,
                difficulty_label=labels.get(word),
                # A fresh SM-2 schedule, per the PRD: due today, so the word
                # reaches the reader's next drill rather than waiting a day.
                sm2_ef=2.5,
                sm2_interval=1,
                sm2_repetitions=0,
                next_review=today,
            )
        )
        added.append(word)

    return added
