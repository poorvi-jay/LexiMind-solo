"""
backend/services/sm2_service.py
F50 — the SM-2 spaced-repetition schedule.

Pure functions over plain values: no database access, no imports from the rest
of the backend. The router owns the row; this owns the arithmetic, which makes
the schedule testable on its own and keeps one copy of the formula.

The implementation follows the PRD's listing exactly. SM-2 variants differ in
their constants and branching, so the details below are deliberate, not
incidental:

  * The new interval is computed from the OLD easiness factor, before EF is
    updated for this answer.
  * A grade below 3 resets the repetition count and the interval, but still
    adjusts EF — a lapse makes the word harder, it does not merely pause it.
  * EF has a floor of 1.3 and no ceiling.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

# The PRD's thresholds.
PASS_QUALITY = 3        # a grade of 3 or more counts as recalled
MIN_EASINESS = 1.3      # EF never drops below this
MASTERED_EF = 2.5       # "mastered" needs an easy word...
MASTERED_INTERVAL = 21  # ...that is also scheduled a long way out


@dataclass(frozen=True)
class Sm2State:
    """The scheduling state of one word. Mirrors the word_bank columns."""

    sm2_ef: float
    sm2_interval: int
    sm2_repetitions: int
    next_review: date
    mastered: bool


def is_mastered(ef: float, interval: int) -> bool:
    """Whether a word counts as mastered.

    Derived rather than stored: it is a pure function of the two scheduling
    values, so a column would only be a copy that could fall out of step.
    """
    return ef >= MASTERED_EF and interval >= MASTERED_INTERVAL


def update_sm2(entry, quality: int, today: date | None = None) -> Sm2State:
    """The next scheduling state for `entry` after answering it with `quality`.

    `entry` is anything carrying sm2_ef, sm2_interval and sm2_repetitions — a
    WordBank row in production, a stub in tests. Nothing is written here; the
    caller applies the result.

    `today` is injectable so a test can schedule from a fixed day instead of
    the real clock.
    """
    ef = entry.sm2_ef
    repetitions = entry.sm2_repetitions
    interval = entry.sm2_interval

    if quality >= PASS_QUALITY:
        # First two successes use fixed steps; after that the interval grows by
        # the easiness factor, which is what makes the schedule expand.
        if repetitions == 0:
            interval = 1
        elif repetitions == 1:
            interval = 6
        else:
            interval = round(interval * ef)
        repetitions += 1
    else:
        # Back to the start of the ladder, due again tomorrow.
        repetitions = 0
        interval = 1

    # Applied on every answer, pass or fail. A grade of 4 leaves EF unchanged;
    # 5 raises it, 3 and below lower it, increasingly steeply.
    ef = max(MIN_EASINESS, ef + 0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
    ef = round(ef, 2)

    return Sm2State(
        sm2_ef=ef,
        sm2_interval=interval,
        sm2_repetitions=repetitions,
        next_review=(today or date.today()) + timedelta(days=interval),
        mastered=is_mastered(ef, interval),
    )
