"""Unit: the Sell (gamification) scoring rules — the extensible {event_type -> points} config.

`points_for` is a pure lookup: a listed event yields its configured points, an unknown event yields
0 (never raises), and adding a new scored event is a one-line `POINTS` entry.
"""
import pytest

from shared.gamify_rules import DEAL_CLOSED_WON, POINTS, points_for


@pytest.mark.unit
def test_points_for_known_event():
    assert points_for(DEAL_CLOSED_WON) == POINTS[DEAL_CLOSED_WON]
    assert points_for("deal.closed_won") > 0  # the v1 scored event is worth a positive credit


@pytest.mark.unit
def test_points_for_unknown_event_is_zero_not_error():
    # An event the config doesn't list is inert (0), never an exception — so a caller can always
    # ask and scoring a new event later is a pure config change.
    assert points_for("deal.lost") == 0
    assert points_for("") == 0
    assert points_for("totally.unknown.event") == 0


@pytest.mark.unit
def test_rules_are_extensible_one_line():
    # The config IS the extension point: a new event is a single mapping entry, picked up by the
    # same pure function with no caller change.
    extended = {**POINTS, "lead.created": 3}
    assert extended["lead.created"] == 3
    # The shipped config stays minimal (v1 scores exactly the closed-won event).
    assert set(POINTS) == {DEAL_CLOSED_WON}
