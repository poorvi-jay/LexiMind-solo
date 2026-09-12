"""
F50 — SM-2 state transitions, checked against the PRD's listing.

Pure arithmetic: no database, no server. These are the hand-verified scenarios
the M3 handoff asks for, and they are the guard against the two details that
differ between SM-2 variants — the interval being computed from the OLD
easiness factor, and a lapse still adjusting EF rather than only resetting.
"""

from dataclasses import dataclass
from datetime import date, timedelta

import pytest

from backend.services.sm2_service import (
    MASTERED_EF,
    MASTERED_INTERVAL,
    MIN_EASINESS,
    is_mastered,
    update_sm2,
)

TODAY = date(2026, 9, 12)


@dataclass
class Entry:
    """Stands in for a WordBank row: update_sm2 only reads these three."""

    sm2_ef: float = 2.5
    sm2_interval: int = 1
    sm2_repetitions: int = 0


def advance(entry: Entry, quality: int) -> Entry:
    state = update_sm2(entry, quality, TODAY)
    return Entry(state.sm2_ef, state.sm2_interval, state.sm2_repetitions)


# ── the interval ladder ────────────────────────────────────────────────
def test_first_success_is_due_in_one_day():
    state = update_sm2(Entry(), 5, TODAY)
    assert (state.sm2_interval, state.sm2_repetitions) == (1, 1)


def test_second_success_jumps_to_six_days():
    state = update_sm2(Entry(2.6, 1, 1), 5, TODAY)
    assert (state.sm2_interval, state.sm2_repetitions) == (6, 2)


def test_third_success_multiplies_by_the_old_easiness_factor():
    """round(6 x 2.7) = 16, using EF *before* this answer raises it to 2.8.

    Multiplying by the new EF would give round(6 x 2.8) = 17. That one-day
    difference is the whole reason the order matters.
    """
    state = update_sm2(Entry(2.7, 6, 2), 5, TODAY)
    assert state.sm2_interval == 16
    assert state.sm2_ef == 2.8


def test_consecutive_successes_grow_the_interval():
    """AC-40: rating a word >= 3 repeatedly must push it further out each time."""
    entry, intervals = Entry(), []
    for _ in range(5):
        entry = advance(entry, 4)
        intervals.append(entry.sm2_interval)
    assert intervals == [1, 6, 15, 38, 95]
    assert all(b > a for a, b in zip(intervals, intervals[1:]))


# ── the easiness factor ────────────────────────────────────────────────
@pytest.mark.parametrize(
    "quality, expected_ef",
    [
        (5, 2.6),   # raises
        (4, 2.5),   # the neutral grade: unchanged
        (3, 2.36),  # scraped through, still harder
        (2, 2.18),
        (1, 1.96),
        (0, 1.70),
    ],
)
def test_easiness_factor_follows_the_prd_formula(quality, expected_ef):
    assert update_sm2(Entry(2.5, 6, 2), quality, TODAY).sm2_ef == expected_ef


def test_easiness_factor_never_falls_below_the_floor():
    entry = Entry()
    for _ in range(6):
        entry = advance(entry, 0)
    assert entry.sm2_ef == MIN_EASINESS


# ── lapses ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("quality", [0, 1, 2])
def test_a_lapse_restarts_the_ladder(quality):
    state = update_sm2(Entry(2.5, 16, 3), quality, TODAY)
    assert (state.sm2_interval, state.sm2_repetitions) == (1, 0)
    assert state.next_review == TODAY + timedelta(days=1)


def test_a_lapse_still_lowers_the_easiness_factor():
    """A missed word becomes permanently harder, not merely rescheduled."""
    assert update_sm2(Entry(2.5, 16, 3), 2, TODAY).sm2_ef == 2.18


# ── scheduling and purity ──────────────────────────────────────────────
def test_next_review_is_today_plus_the_new_interval():
    state = update_sm2(Entry(2.5, 6, 2), 5, TODAY)
    assert state.sm2_interval == 15
    assert state.next_review == TODAY + timedelta(days=15)


def test_the_entry_is_never_mutated():
    """The caller applies the result; nothing is written here."""
    entry = Entry(2.5, 6, 2)
    update_sm2(entry, 5, TODAY)
    assert (entry.sm2_ef, entry.sm2_interval, entry.sm2_repetitions) == (2.5, 6, 2)


# ── mastery, which is derived rather than stored ───────────────────────
@pytest.mark.parametrize(
    "ef, interval, expected",
    [
        (MASTERED_EF, MASTERED_INTERVAL, True),      # exactly at both thresholds
        (MASTERED_EF - 0.1, MASTERED_INTERVAL, False),
        (MASTERED_EF + 0.1, MASTERED_INTERVAL - 1, False),
        (2.9, 95, True),
        (1.3, 1, False),
    ],
)
def test_mastery_needs_both_thresholds(ef, interval, expected):
    assert is_mastered(ef, interval) is expected
