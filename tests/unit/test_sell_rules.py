"""Unit: Sell (gamification) display rules — XP→level mapping + the activity streak.

Pure functions in shared/gamify_rules.py: no I/O, no DB. These are the single source of truth the
/sell/me surface derives a rep's level/progress/streak from, so the math is pinned here.
"""
import pytest

from shared.gamify_rules import (
    XP_PER_LEVEL,
    level_for,
    level_progress,
    streak_from_days,
)


@pytest.mark.unit
def test_level_for_starts_at_one_and_climbs_each_band():
    assert level_for(0) == 1            # a brand-new rep is level 1, never 0
    assert level_for(XP_PER_LEVEL - 1) == 1
    assert level_for(XP_PER_LEVEL) == 2
    assert level_for(2 * XP_PER_LEVEL) == 3
    # Negative xp can never happen (points are non-negative), but never crash / go below 1.
    assert level_for(-5) == 1


@pytest.mark.unit
def test_level_progress_reports_into_band_and_remaining():
    # Halfway into the second band.
    xp = XP_PER_LEVEL + (XP_PER_LEVEL // 2)
    prog = level_progress(xp)
    assert prog["level"] == 2
    assert prog["xp"] == xp
    assert prog["into_level"] == XP_PER_LEVEL // 2
    assert prog["span"] == XP_PER_LEVEL
    assert prog["to_next"] == XP_PER_LEVEL - (XP_PER_LEVEL // 2)
    assert prog["next_level_xp"] == 2 * XP_PER_LEVEL
    assert prog["pct"] == pytest.approx(0.5)


@pytest.mark.unit
def test_level_progress_at_a_band_boundary_is_zero_into_the_new_band():
    prog = level_progress(XP_PER_LEVEL)
    assert prog["level"] == 2
    assert prog["into_level"] == 0
    assert prog["pct"] == pytest.approx(0.0)


@pytest.mark.unit
def test_streak_counts_consecutive_days_ending_today():
    days = {"2026-06-12", "2026-06-11", "2026-06-10", "2026-06-08"}
    # today + the two days before it are consecutive; the gap at 06-09 stops the run.
    assert streak_from_days(days, today="2026-06-12") == 3


@pytest.mark.unit
def test_streak_is_zero_when_no_activity_today():
    # A streak is "alive" only if today is present — yesterday-only is a broken streak (0).
    days = {"2026-06-11", "2026-06-10"}
    assert streak_from_days(days, today="2026-06-12") == 0


@pytest.mark.unit
def test_streak_handles_empty_and_single_day():
    assert streak_from_days(set(), today="2026-06-12") == 0
    assert streak_from_days({"2026-06-12"}, today="2026-06-12") == 1
