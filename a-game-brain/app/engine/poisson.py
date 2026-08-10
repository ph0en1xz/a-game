"""Poisson goal model (ADR 0010 §5, §10, §11, §12, §13).

Turns decayed historical goal data into a per-team attack and defence strength,
multiplies those by the league's own home and away scoring rates to get two
expected goal counts, and reads every reported probability off the resulting
score matrix (§12).

Runs in parallel with Elo, not downstream of it (§11). Elo is rating state and
cold start; this is where the numbers come from.

Pure like `elo.py` — match records in, numbers out. No pool, no awaits.

Known limitation (§13): treating the two scorelines as independent Poissons
underestimates draws and low-scoring results. Dixon-Coles is the standard fix
and is deliberately deferred until stored calibration data shows it is needed.
"""

import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

from app.engine.elo import MatchResult
from app.engine.params import HALF_LIFE_DAYS, MAX_GOALS, MIN_SAMPLE


@dataclass
class Strengths:
    """Fitted goal model for one competition, as of a point in time.

    Attributes:
        league_home: Weighted average goals scored by the home side.
        league_away: Weighted average goals scored by the away side. The gap
            between the two IS home advantage on this side of the engine — it
            is not applied per team.
        attack: Goals scored relative to an average side, keyed by
            (team_id, competition_id). 1.0 is league average, 1.2 is twenty
            percent more.
        defence: Goals conceded relative to an average side, same key. LOWER is
            better here — 0.8 means a fifth fewer than average go in.
    """

    league_home: float = 0.0
    league_away: float = 0.0
    attack: dict[tuple[int, int], float] = field(default_factory=dict)
    defence: dict[tuple[int, int], float] = field(default_factory=dict)


@dataclass
class ScoreProbabilities:
    """Everything the prediction row stores, read off one score matrix."""

    lambda_home: float
    lambda_away: float
    prob_home: float
    prob_draw: float
    prob_away: float
    over_2_5: float
    btts: float
    most_likely_scores: list[dict[str, float | int]]


def _weight(match_date: datetime, as_of: datetime) -> float:
    """Exponential recency weight for one match (§5).

    Args:
        match_date (datetime): When the match was played.
        as_of (datetime): The point the model is being fitted for. Must be
            timezone-aware to match the Postgres TIMESTAMPTZ these come from.

    Returns:
        float: 1.0 for a match played today, 0.5 at one half-life, approaching
        0 for anything ancient. Future matches clamp to 1.0 rather than
        exceeding it.
    """
    age_days = max((as_of - match_date).total_seconds() / 86400.0, 0.0)
    return 0.5 ** (age_days / HALF_LIFE_DAYS)


def _shrink(observed: float, expected: float, sample: float) -> float:
    """Blend a team's own record toward the league average of 1.0 (§10).

    The weight is sample / (sample + MIN_SAMPLE), so a team with MIN_SAMPLE
    effective matches sits halfway between its own record and the prior, and
    converges on its own record from there. Nothing is ever fully trusted at
    two games and nothing stays anchored to the prior forever.

    Args:
        observed (float): Weighted goals actually scored (or conceded).
        expected (float): Weighted goals an average side would have managed in
            the same fixtures.
        sample (float): Effective number of matches, i.e. the sum of weights.

    Returns:
        float: The shrunk strength. 1.0 when there is nothing to go on.
    """
    if expected <= 0 or sample <= 0:
        return 1.0

    confidence = sample / (sample + MIN_SAMPLE)
    return confidence * (observed / expected) + (1 - confidence) * 1.0


def fit(matches: Iterable[MatchResult], as_of: datetime) -> Strengths:
    """Fit attack and defence strengths from a decayed match history.

    One attack and one defence number per team, with the home/away split
    carried by the two league rates rather than by four numbers per team. Three
    seasons is thin enough that per-team home and away splits would be fitted
    on roughly 57 matches each — more parameters than the data identifies.

    Args:
        matches (Iterable[MatchResult]): Finished matches only, any competition.
        as_of (datetime): The point to decay toward. For a live prediction this
            is now; for a walk-forward backtest (§17) it is the matchday being
            predicted, which is what stops the future leaking in.

    Returns:
        Strengths: Fitted model. Teams absent from the history are simply not in
        the maps, and `predict` treats them as league average.
    """
    strengths = Strengths()

    history = list(matches)
    if not history:
        return strengths

    weights = [_weight(m.utc_date, as_of) for m in history]
    total_weight = sum(weights)
    if total_weight <= 0:
        return strengths

    strengths.league_home = (
        sum(w * m.home_goals for w, m in zip(weights, history)) / total_weight
    )
    strengths.league_away = (
        sum(w * m.away_goals for w, m in zip(weights, history)) / total_weight
    )

    # Per team: what it actually managed, against what an average side would
    # have managed in the same fixtures. The comparison is what makes the ratio
    # a strength rather than a raw goal count.
    scored: dict[tuple[int, int], float] = {}
    conceded: dict[tuple[int, int], float] = {}
    scored_baseline: dict[tuple[int, int], float] = {}
    conceded_baseline: dict[tuple[int, int], float] = {}
    sample: dict[tuple[int, int], float] = {}

    for weight, match in zip(weights, history):
        home_key = (match.home_team_id, match.competition_id)
        away_key = (match.away_team_id, match.competition_id)

        for key, goals_for, goals_against, baseline_for, baseline_against in (
            (
                home_key,
                match.home_goals,
                match.away_goals,
                strengths.league_home,
                strengths.league_away,
            ),
            (
                away_key,
                match.away_goals,
                match.home_goals,
                strengths.league_away,
                strengths.league_home,
            ),
        ):
            scored[key] = scored.get(key, 0.0) + weight * goals_for
            conceded[key] = conceded.get(key, 0.0) + weight * goals_against
            scored_baseline[key] = scored_baseline.get(key, 0.0) + weight * baseline_for
            conceded_baseline[key] = (
                conceded_baseline.get(key, 0.0) + weight * baseline_against
            )
            sample[key] = sample.get(key, 0.0) + weight

    for key, effective_matches in sample.items():
        strengths.attack[key] = _shrink(
            scored[key], scored_baseline[key], effective_matches
        )
        strengths.defence[key] = _shrink(
            conceded[key], conceded_baseline[key], effective_matches
        )

    return strengths


def expected_goals(
    strengths: Strengths,
    home_team_id: int,
    away_team_id: int,
    competition_id: int,
) -> tuple[float, float]:
    """Expected goals for both sides in one fixture.

    Each side's rate is the league rate for its end of the pitch, scaled by its
    own attack and by the opponent's defence. A strong attack against a leaky
    defence multiplies; against a good one the two pull in opposite directions.

    Args:
        strengths (Strengths): Output of `fit`.
        home_team_id (int): Home side.
        away_team_id (int): Away side.
        competition_id (int): The competition both are playing in.

    Returns:
        tuple[float, float]: (lambda_home, lambda_away). Unknown teams fall back
        to 1.0 on both strengths, which makes them exactly league average — the
        honest answer when there is no history at all.
    """
    home_key = (home_team_id, competition_id)
    away_key = (away_team_id, competition_id)

    lambda_home = (
        strengths.league_home
        * strengths.attack.get(home_key, 1.0)
        * strengths.defence.get(away_key, 1.0)
    )
    lambda_away = (
        strengths.league_away
        * strengths.attack.get(away_key, 1.0)
        * strengths.defence.get(home_key, 1.0)
    )

    return lambda_home, lambda_away


def _pmf(rate: float, goals: int) -> float:
    """Poisson probability of exactly `goals` given expected `rate`."""
    return math.exp(-rate) * rate**goals / math.factorial(goals)


def score_probabilities(lambda_home: float, lambda_away: float) -> ScoreProbabilities:
    """Build the score matrix and read every reported probability off it (§12).

    One matrix, one source of truth. Draws need no separate treatment because
    they are just its diagonal — which is the reason ADR 0010 §11 keeps Elo out
    of the probability path in the first place.

    Args:
        lambda_home (float): Expected home goals.
        lambda_away (float): Expected away goals.

    Returns:
        ScoreProbabilities: 1X2, over 2.5, both teams to score, the three most
        likely scorelines, and the two rates they came from.
    """
    home_pmf = [_pmf(lambda_home, g) for g in range(MAX_GOALS + 1)]
    away_pmf = [_pmf(lambda_away, g) for g in range(MAX_GOALS + 1)]

    prob_home = prob_draw = prob_away = 0.0
    over_2_5 = 0.0
    btts = 0.0
    cells: list[tuple[int, int, float]] = []

    for home_goals, p_home in enumerate(home_pmf):
        for away_goals, p_away in enumerate(away_pmf):
            probability = p_home * p_away
            cells.append((home_goals, away_goals, probability))

            if home_goals > away_goals:
                prob_home += probability
            elif home_goals < away_goals:
                prob_away += probability
            else:
                prob_draw += probability

            if home_goals + away_goals > 2:
                over_2_5 += probability
            if home_goals > 0 and away_goals > 0:
                btts += probability

    # The truncated tail costs a fraction of a percent, and the 1X2 numbers are
    # stored under a CHECK that they sum to 1. Normalising here is what makes
    # that constraint a real assertion about the matrix rather than something
    # the writer has to work around.
    total = prob_home + prob_draw + prob_away

    cells.sort(key=lambda cell: cell[2], reverse=True)
    most_likely = [
        {"home": home_goals, "away": away_goals, "prob": round(probability / total, 4)}
        for home_goals, away_goals, probability in cells[:3]
    ]

    return ScoreProbabilities(
        lambda_home=lambda_home,
        lambda_away=lambda_away,
        prob_home=prob_home / total,
        prob_draw=prob_draw / total,
        prob_away=prob_away / total,
        over_2_5=over_2_5 / total,
        btts=btts / total,
        most_likely_scores=most_likely,
    )
