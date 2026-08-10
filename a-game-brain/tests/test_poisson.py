"""Poisson goal model (`app/engine/poisson.py`, ADR 0010 §5, §10, §11, §12).

Every probability the product reports is read off the score matrix these
functions build, so an error here is invisible in the logs and shows up only as a
bad Brier score months later.

The sum-to-1 property gets its own test because the prediction table has a CHECK
constraint on it — a matrix that doesn't normalise fails at INSERT, in a
transaction, at 06:00.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.engine import poisson
from app.engine.params import HALF_LIFE_DAYS, MAX_GOALS
from tests.conftest import KICKOFF, result

PL = 2021
AS_OF = KICKOFF


# --------------------------------------------------------------------------
# _weight — recency decay
# --------------------------------------------------------------------------


def test_a_match_played_now_has_full_weight():
    assert poisson._weight(AS_OF, AS_OF) == pytest.approx(1.0)


def test_one_half_life_halves_the_weight():
    older = AS_OF - timedelta(days=HALF_LIFE_DAYS)

    assert poisson._weight(older, AS_OF) == pytest.approx(0.5)


def test_two_half_lives_quarter_it():
    older = AS_OF - timedelta(days=2 * HALF_LIFE_DAYS)

    assert poisson._weight(older, AS_OF) == pytest.approx(0.25)


def test_a_future_match_clamps_to_one():
    """Never above 1.0. A clock skew or an as_of in the past would otherwise give
    a single fixture more weight than any real match."""
    future = AS_OF + timedelta(days=30)

    assert poisson._weight(future, AS_OF) == pytest.approx(1.0)


def test_weight_decreases_monotonically():
    ages = [0, 30, 180, 365, 1000]
    weights = [poisson._weight(AS_OF - timedelta(days=d), AS_OF) for d in ages]

    assert weights == sorted(weights, reverse=True)


# --------------------------------------------------------------------------
# _shrink — the league prior
# --------------------------------------------------------------------------


def test_no_evidence_shrinks_to_league_average():
    assert poisson._shrink(0.0, 0.0, 0.0) == 1.0
    assert poisson._shrink(5.0, 0.0, 3.0) == 1.0


def test_a_large_sample_converges_on_the_observed_ratio():
    """With enough matches the prior stops mattering."""
    shrunk = poisson._shrink(observed=200.0, expected=100.0, sample=1000.0)

    assert shrunk == pytest.approx(2.0, rel=0.02)


def test_a_small_sample_is_pulled_toward_the_prior():
    """Six goals in two games must not be modelled as three a game (§10)."""
    shrunk = poisson._shrink(observed=6.0, expected=2.0, sample=2.0)

    assert 1.0 < shrunk < 3.0


def test_shrinkage_weakens_as_the_sample_grows():
    small = poisson._shrink(4.0, 2.0, 2.0)
    large = poisson._shrink(40.0, 20.0, 20.0)

    assert abs(large - 2.0) < abs(small - 2.0)


# --------------------------------------------------------------------------
# fit
# --------------------------------------------------------------------------


def test_fit_on_no_history_returns_an_empty_model():
    strengths = poisson.fit([], AS_OF)

    assert strengths.attack == {}
    assert strengths.defence == {}
    assert strengths.league_home == 0.0


def test_a_history_that_decays_to_nothing_returns_an_empty_model():
    """Every weight underflows to zero, so the totals can't be divided by.

    Only reachable with centuries-old dates, which means a corrupt `utc_date`
    rather than real football. The guard turns a ZeroDivisionError deep in the fit
    into an empty model the caller already knows how to handle.
    """
    ancient = [result(1, 1, 2, 3, 0, days_ago=400_000)]

    strengths = poisson.fit(ancient, AS_OF)

    assert strengths.attack == {}
    assert strengths.league_home == 0.0


def test_league_rates_reflect_home_advantage(history):
    """The home/away gap IS home advantage on this side of the engine — it is not
    applied per team."""
    strengths = poisson.fit(history, AS_OF)

    assert strengths.league_home > strengths.league_away > 0


def test_every_team_gets_an_attack_and_a_defence(history):
    strengths = poisson.fit(history, AS_OF)

    for team in (1, 2, 3):
        assert (team, PL) in strengths.attack
        assert (team, PL) in strengths.defence


def test_the_better_side_has_the_stronger_attack(history):
    strengths = poisson.fit(history, AS_OF)

    assert strengths.attack[(1, PL)] > strengths.attack[(3, PL)]


def test_lower_defence_is_better(history):
    """Defence is goals conceded relative to average, so team 1 — which concedes
    least — must have the lowest number."""
    strengths = poisson.fit(history, AS_OF)

    assert strengths.defence[(1, PL)] < strengths.defence[(3, PL)]


def test_recent_form_outweighs_old_form():
    """Same fixtures, same scores, only the dates differ. The side whose thrashing
    is recent must come out stronger.

    Teams 3 and 4 are ballast and exist only to keep the league baseline honest.
    Without them team 1 would be the entire home record the league average is
    computed from, its attack would be its own record divided by itself, and the
    test would compare 1.0 to 1.0 no matter what the decay did.
    """
    ballast = [
        result(90, 3, 4, 1, 1, days_ago=5),
        result(91, 3, 4, 1, 1, days_ago=700),
    ]
    recent = [
        result(1, 1, 2, 5, 0, days_ago=5),
        result(2, 1, 2, 1, 1, days_ago=700),
        *ballast,
    ]
    stale = [
        result(1, 1, 2, 5, 0, days_ago=700),
        result(2, 1, 2, 1, 1, days_ago=5),
        *ballast,
    ]

    assert (
        poisson.fit(recent, AS_OF).attack[(1, PL)]
        > poisson.fit(stale, AS_OF).attack[(1, PL)]
    )


def test_strengths_are_competition_scoped():
    matches = [
        result(1, 1, 2, 4, 0, days_ago=10),
        result(2, 1, 2, 0, 4, days_ago=10, competition_id=2001),
    ]

    strengths = poisson.fit(matches, AS_OF)

    assert (1, PL) in strengths.attack
    assert (1, 2001) in strengths.attack
    assert strengths.attack[(1, PL)] != strengths.attack[(1, 2001)]


def test_as_of_changes_the_model(history):
    """`as_of` is the decay anchor, and moving it must move the fit.

    Worth being precise about what this argument is and isn't. It does not filter
    the history — `fetch_match_history(before=...)` does that, and that query is
    what actually stops the walk-forward backtest (§17) seeing the future. What
    `as_of` decides is how old each match counts as, so a stale anchor silently
    reweights every strength.
    """
    now = poisson.fit(history, AS_OF)
    much_later = poisson.fit(history, AS_OF + timedelta(days=400))

    assert now.attack[(1, PL)] != much_later.attack[(1, PL)]


def test_an_anchor_before_every_match_flattens_the_weights():
    """All weights clamp to 1.0, so the fit is a plain unweighted average.

    A clock skew or a backtest anchored wrongly lands here — it doesn't crash, it
    just quietly throws the recency model away.
    """
    matches = [
        result(1, 1, 2, 3, 0, days_ago=400),
        result(2, 3, 4, 0, 3, days_ago=10),
    ]

    flat = poisson.fit(matches, AS_OF - timedelta(days=500))

    assert flat.league_home == pytest.approx((3 + 0) / 2)


# --------------------------------------------------------------------------
# expected_goals
# --------------------------------------------------------------------------


def test_unknown_teams_come_out_league_average(history):
    """The honest answer with no history: exactly the league rates."""
    strengths = poisson.fit(history, AS_OF)

    home, away = poisson.expected_goals(strengths, 999, 998, PL)

    assert home == pytest.approx(strengths.league_home)
    assert away == pytest.approx(strengths.league_away)


def test_a_strong_attack_against_a_weak_defence_multiplies(history):
    strengths = poisson.fit(history, AS_OF)

    strong_home, _ = poisson.expected_goals(strengths, 1, 3, PL)
    weak_home, _ = poisson.expected_goals(strengths, 3, 1, PL)

    assert strong_home > weak_home


def test_home_and_away_rates_use_different_league_baselines(history):
    """The same fixture reversed must not produce mirrored numbers, because the
    league home rate is higher than the away rate."""
    strengths = poisson.fit(history, AS_OF)

    home_a, _ = poisson.expected_goals(strengths, 1, 2, PL)
    _, away_b = poisson.expected_goals(strengths, 2, 1, PL)

    # Team 1 at home vs team 2 away, against team 1 away vs team 2 at home.
    # Identical strengths on both sides, so any difference is the league split.
    assert home_a != pytest.approx(away_b)


def test_expected_goals_are_non_negative(history):
    strengths = poisson.fit(history, AS_OF)

    home, away = poisson.expected_goals(strengths, 1, 3, PL)

    assert home >= 0 and away >= 0


# --------------------------------------------------------------------------
# _pmf
# --------------------------------------------------------------------------


def test_pmf_matches_the_poisson_formula():
    # P(0 | lambda) = e^-lambda
    assert poisson._pmf(1.5, 0) == pytest.approx(0.22313016)
    assert poisson._pmf(1.5, 1) == pytest.approx(0.33469524)
    assert poisson._pmf(1.5, 2) == pytest.approx(0.25102143)


def test_pmf_sums_to_one_over_the_matrix_range():
    total = sum(poisson._pmf(1.4, g) for g in range(MAX_GOALS + 1))

    assert total == pytest.approx(1.0, abs=1e-6)


# --------------------------------------------------------------------------
# score_probabilities
# --------------------------------------------------------------------------


def test_the_three_outcomes_sum_to_one():
    """The prediction table has a CHECK on exactly this, with 0.0005 tolerance.

    Normalising in the engine is what makes that constraint a real assertion about
    the matrix rather than something the writer has to work around.
    """
    probs = poisson.score_probabilities(1.31, 1.21)

    assert probs.prob_home + probs.prob_draw + probs.prob_away == pytest.approx(1.0)


def test_it_still_sums_to_one_at_an_absurd_rate():
    """High lambdas push mass past MAX_GOALS. If truncation weren't normalised
    away, this is where the CHECK would start failing."""
    probs = poisson.score_probabilities(6.0, 5.0)

    assert probs.prob_home + probs.prob_draw + probs.prob_away == pytest.approx(1.0)


def test_the_favourite_is_the_side_with_the_higher_rate():
    probs = poisson.score_probabilities(2.1, 0.9)

    assert probs.prob_home > probs.prob_away


def test_equal_rates_give_symmetric_outcomes():
    probs = poisson.score_probabilities(1.4, 1.4)

    assert probs.prob_home == pytest.approx(probs.prob_away)


def test_low_scoring_games_favour_the_draw():
    tight = poisson.score_probabilities(0.7, 0.7)
    open_game = poisson.score_probabilities(2.5, 2.5)

    assert tight.prob_draw > open_game.prob_draw


def test_over_2_5_rises_with_the_goal_rates():
    assert (
        poisson.score_probabilities(2.4, 2.2).over_2_5
        > poisson.score_probabilities(0.8, 0.7).over_2_5
    )


def test_btts_rises_with_the_goal_rates():
    assert (
        poisson.score_probabilities(2.0, 1.8).btts
        > poisson.score_probabilities(0.6, 0.5).btts
    )


def test_every_probability_is_a_probability():
    probs = poisson.score_probabilities(1.6, 1.2)

    for value in (
        probs.prob_home,
        probs.prob_draw,
        probs.prob_away,
        probs.over_2_5,
        probs.btts,
    ):
        assert 0.0 <= value <= 1.0


def test_three_most_likely_scores_are_returned_in_order():
    probs = poisson.score_probabilities(1.31, 1.21)
    scores = probs.most_likely_scores

    assert len(scores) == 3
    assert [s["prob"] for s in scores] == sorted(
        (s["prob"] for s in scores), reverse=True
    )


def test_most_likely_scores_carry_integer_goals():
    """These land in JSONB and are rendered straight into the prompt as `1-1`."""
    for score in poisson.score_probabilities(1.4, 1.1).most_likely_scores:
        assert isinstance(score["home"], int)
        assert isinstance(score["away"], int)


def test_the_top_scoreline_for_low_rates_is_nil_nil():
    top = poisson.score_probabilities(0.5, 0.4).most_likely_scores[0]

    assert (top["home"], top["away"]) == (0, 0)


def test_the_lambdas_are_carried_through():
    """The stored lambdas regenerate the whole matrix, so a new market can be
    derived later without replaying the engine."""
    probs = poisson.score_probabilities(1.31, 1.21)

    assert probs.lambda_home == 1.31
    assert probs.lambda_away == 1.21


def test_scores_are_rounded_to_the_stored_scale():
    """NUMERIC(5,4) in the JSONB payload — rounding here keeps the written value
    and the computed one identical."""
    for score in poisson.score_probabilities(1.7, 1.3).most_likely_scores:
        assert score["prob"] == round(float(score["prob"]), 4)


# --------------------------------------------------------------------------
# End to end through the two models
# --------------------------------------------------------------------------


def test_the_full_pipeline_produces_a_usable_prediction(history):
    strengths = poisson.fit(history, AS_OF)
    home, away = poisson.expected_goals(strengths, 1, 3, PL)
    probs = poisson.score_probabilities(home, away)

    assert probs.prob_home > probs.prob_away
    assert probs.prob_home + probs.prob_draw + probs.prob_away == pytest.approx(1.0)
    assert len(probs.most_likely_scores) == 3


def test_fit_is_deterministic(history):
    """Same input, same numbers — a backtest that isn't reproducible proves
    nothing."""
    first = poisson.fit(history, AS_OF)
    second = poisson.fit(list(reversed(history)), AS_OF)

    assert first.attack == pytest.approx(second.attack)
    assert first.league_home == pytest.approx(second.league_home)


def test_naive_timestamps_are_rejected():
    """`utc_date` comes from a TIMESTAMPTZ column and must stay aware.

    Python refuses to subtract an aware datetime from a naive one, so this raises
    rather than silently mis-weighting by whatever the local offset happens to be.
    Relying on that is fine — but only because it's asserted here.
    """
    aware = datetime.now(UTC)
    naive = aware.replace(tzinfo=None)

    with pytest.raises(TypeError):
        poisson._weight(naive, aware)
