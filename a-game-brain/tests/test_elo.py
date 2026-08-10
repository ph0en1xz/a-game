"""Elo rating loop (`app/engine/elo.py`, ADR 0010 §2, §6, §8, §9).

Pure functions with exact expected values, so these are the cheapest tests in the
repo and the ones guarding the most. Everything downstream — the stored ratings,
the cross-check on the Poisson output, the backtest — assumes this loop is
deterministic and zero-sum.
"""

import math

import pytest

from app.engine import elo
from app.engine.params import (
    DEFAULT_RATING,
    HOME_ADVANTAGE,
    K_FACTOR,
    PROMOTED_PENALTY,
)
from tests.conftest import result

PL = 2021


# --------------------------------------------------------------------------
# expected_score
# --------------------------------------------------------------------------


def test_equal_ratings_favour_the_home_side():
    """Home advantage is applied inside expected_score, not by the caller.

    Two identical ratings must not come out at 0.5, or HOME_ADVANTAGE isn't doing
    anything.
    """
    assert elo.expected_score(1500, 1500) > 0.5


def test_home_advantage_is_worth_exactly_its_rating_points():
    """A home side rated HOME_ADVANTAGE below its opponent is an even match."""
    assert elo.expected_score(1500 - HOME_ADVANTAGE, 1500) == pytest.approx(0.5)


def test_expected_score_is_bounded():
    assert 0.0 < elo.expected_score(1000, 2000) < 0.5
    assert 0.5 < elo.expected_score(2000, 1000) < 1.0


def test_four_hundred_points_is_ten_to_one():
    """The defining property of the Elo curve: a 400-point gap is 10:1 odds."""
    p = elo.expected_score(1500 + 400 - HOME_ADVANTAGE, 1500)

    assert p / (1 - p) == pytest.approx(10.0)


# --------------------------------------------------------------------------
# goal_difference_multiplier
# --------------------------------------------------------------------------


def test_one_goal_margin_is_the_baseline():
    assert elo.goal_difference_multiplier(1) == pytest.approx(1.0)


def test_bigger_margins_count_for_more():
    assert elo.goal_difference_multiplier(3) > elo.goal_difference_multiplier(1)


def test_scaling_is_damped_not_linear():
    """A 4-0 must not be worth four times a 1-0 — strong sides win big often, and
    undamped scaling inflates them (§6)."""
    assert elo.goal_difference_multiplier(4) < 4 * elo.goal_difference_multiplier(1)
    assert elo.goal_difference_multiplier(4) == pytest.approx(math.log(5) / math.log(2))


# --------------------------------------------------------------------------
# seed_rating
# --------------------------------------------------------------------------


def test_seed_is_the_default_when_nothing_is_rated():
    assert elo.seed_rating({}, PL) == DEFAULT_RATING


def test_seed_is_league_average_minus_the_penalty():
    ratings = {(1, PL): 1600.0, (2, PL): 1400.0}

    assert elo.seed_rating(ratings, PL) == 1500.0 - PROMOTED_PENALTY


def test_seed_ignores_other_competitions():
    """Ratings are competition-scoped — points don't transfer between leagues (§2).

    A Championship average must not drag the Premier League seed around.
    """
    ratings = {(1, PL): 1600.0, (2, PL): 1400.0, (3, 2016): 900.0}

    assert elo.seed_rating(ratings, PL) == 1500.0 - PROMOTED_PENALTY


def test_seed_falls_back_when_the_competition_is_empty():
    assert elo.seed_rating({(1, 2016): 1700.0}, PL) == DEFAULT_RATING


# --------------------------------------------------------------------------
# apply_match
# --------------------------------------------------------------------------


def test_a_win_moves_both_ratings_in_opposite_directions():
    ratings = {(1, PL): 1500.0, (2, PL): 1500.0}

    elo.apply_match(ratings, result(1, 1, 2, 2, 1))

    assert ratings[(1, PL)] > 1500.0
    assert ratings[(2, PL)] < 1500.0


def test_the_update_is_zero_sum():
    """What one side gains the other loses — this is what holds the league average
    steady across a season, and what makes seed_rating's drift argument true."""
    ratings = {(1, PL): 1500.0, (2, PL): 1420.0}
    before = sum(ratings.values())

    elo.apply_match(ratings, result(1, 1, 2, 3, 0))

    assert sum(ratings.values()) == pytest.approx(before)


def test_snapshot_holds_the_pre_match_ratings():
    """The prediction row stores ratings *at kickoff*. Taking the snapshot after
    the update would leak the result into the number."""
    ratings = {(1, PL): 1600.0, (2, PL): 1400.0}

    snapshot = elo.apply_match(ratings, result(7, 1, 2, 1, 0))

    assert snapshot.match_id == 7
    assert snapshot.home == 1600.0
    assert snapshot.away == 1400.0
    assert ratings[(1, PL)] != 1600.0


def test_a_draw_still_moves_the_ratings():
    """log(1)/log(2) is 0, so a draw scaled by goal difference would be a no-op.

    A draw between mismatched sides is informative and must move both ratings —
    the multiplier is forced to 1.0 for level scores.
    """
    ratings = {(1, PL): 1700.0, (2, PL): 1300.0}

    elo.apply_match(ratings, result(1, 1, 2, 1, 1))

    assert ratings[(1, PL)] < 1700.0
    assert ratings[(2, PL)] > 1300.0


def test_a_draw_between_equals_barely_moves_anything():
    ratings = {(1, PL): 1500.0, (2, PL): 1500.0}

    elo.apply_match(ratings, result(1, 1, 2, 0, 0))

    # The home side was favoured, so a draw costs it a little.
    assert ratings[(1, PL)] < 1500.0
    assert abs(ratings[(1, PL)] - 1500.0) < K_FACTOR


def test_an_upset_moves_more_than_an_expected_result():
    strong_wins = {(1, PL): 1800.0, (2, PL): 1200.0}
    weak_wins = {(1, PL): 1800.0, (2, PL): 1200.0}

    elo.apply_match(strong_wins, result(1, 1, 2, 1, 0))
    elo.apply_match(weak_wins, result(2, 1, 2, 0, 1))

    assert abs(weak_wins[(1, PL)] - 1800.0) > abs(strong_wins[(1, PL)] - 1800.0)


def test_unrated_teams_are_seeded_before_the_update():
    ratings: dict[tuple[int, int], float] = {}

    snapshot = elo.apply_match(ratings, result(1, 1, 2, 1, 0))

    assert snapshot.home == DEFAULT_RATING
    assert snapshot.away == DEFAULT_RATING
    assert (1, PL) in ratings and (2, PL) in ratings


def test_both_new_teams_get_the_same_seed():
    """Seeding is computed once, before either insert.

    Doing it sequentially would let the first team move the average the second is
    seeded from, making the pair order-dependent for no reason.
    """
    ratings = {(9, PL): 1600.0}

    snapshot = elo.apply_match(ratings, result(1, 1, 2, 1, 0))

    assert snapshot.home == snapshot.away == 1600.0 - PROMOTED_PENALTY


def test_a_team_keeps_its_rating_across_competitions():
    """Same team id, two competitions, two independent ratings (§2)."""
    ratings = {(1, PL): 1800.0}

    elo.apply_match(ratings, result(1, 1, 2, 0, 3, competition_id=2001))

    assert ratings[(1, PL)] == 1800.0
    assert ratings[(1, 2001)] < DEFAULT_RATING


# --------------------------------------------------------------------------
# run
# --------------------------------------------------------------------------


def test_run_returns_one_snapshot_per_match(history):
    state = elo.run(history)

    assert len(state.snapshots) == len(history)
    assert [s.match_id for s in state.snapshots] == [1, 2, 3, 4, 5, 6]


def test_run_rates_every_team_it_saw(history):
    state = elo.run(history)

    assert set(state.ratings) == {(1, PL), (2, PL), (3, PL)}


def test_run_orders_by_date_not_input_order(history):
    """Ratings are path-dependent, so replay order is the result.

    Feeding the same matches shuffled must produce identical ratings, or no
    backtest is reproducible.
    """
    forward = elo.run(history).ratings
    backward = elo.run(list(reversed(history))).ratings

    assert forward == pytest.approx(backward)


def test_run_breaks_ties_on_match_id():
    """Several fixtures share a kickoff time. Without the id tie-break, two replays
    of identical data drift apart."""
    same_time = [
        result(20, 1, 2, 1, 0, days_ago=10),
        result(10, 3, 4, 2, 2, days_ago=10),
    ]

    first = elo.run(same_time)
    second = elo.run(list(reversed(same_time)))

    assert [s.match_id for s in first.snapshots] == [10, 20]
    assert first.ratings == pytest.approx(second.ratings)


def test_run_on_an_empty_history_is_empty():
    state = elo.run([])

    assert state.ratings == {}
    assert state.snapshots == []


def test_the_stronger_team_ends_up_rated_higher(history):
    """Team 1 wins or draws everything; team 3 loses everything."""
    ratings = elo.run(history).ratings

    assert ratings[(1, PL)] > ratings[(2, PL)] > ratings[(3, PL)]


def test_the_league_average_is_preserved(history):
    """Zero-sum across a whole replay, given every team starts level.

    Seeded up front rather than letting `run` do it, because seeding is what makes
    the average drift — see the next test. Separating the two means a failure here
    is the update loop's fault and nothing else's.
    """
    ratings = {(team, PL): DEFAULT_RATING for team in (1, 2, 3)}

    for match in history:
        elo.apply_match(ratings, match)

    assert sum(ratings.values()) / len(ratings) == pytest.approx(DEFAULT_RATING)


def test_seeding_a_late_arrival_drags_the_average_down(history):
    """The reason seed_rating computes the average live instead of hardcoding 1500.

    Teams 1 and 2 start at the default; team 3 only appears in the second match and
    is seeded at the average minus the promotion penalty. So a full replay does NOT
    average out at 1500, and anchoring new teams to a constant would compound that
    every season.
    """
    ratings = elo.run(history).ratings
    average = sum(ratings.values()) / len(ratings)

    assert average < DEFAULT_RATING
    assert average == pytest.approx(DEFAULT_RATING - PROMOTED_PENALTY / 3)
