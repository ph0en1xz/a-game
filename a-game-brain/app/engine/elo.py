"""Elo ratings (ADR 0010 §2, §6, §8, §9).

One number per (team, competition) representing strength, updated after every
match by how far the result missed the rating's own prediction. Elo is rating
state and cold-start handling only — none of the reported probabilities come
from it (§12). It appears in the payload as context and a cross-check (§14).

Deliberately pure: this module takes match records and returns numbers. No pool,
no awaits, no I/O. The history query lives in `app/db.py`. That keeps the loop
testable on a handful of hand-written matches, which matters most when the
walk-forward backtest (§17) calls run() once per matchday.

Parameters come from `app.engine.params` — import them as you fill the bodies.
"""

import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

from app.engine.params import (
    DEFAULT_RATING,
    HOME_ADVANTAGE,
    K_FACTOR,
    PROMOTED_PENALTY,
)


@dataclass
class MatchResult:
    """One finished match, in the shape the rating loop needs.

    A plain dataclass rather than a pydantic model: these come from our own
    Postgres rows, not an external boundary, and the loop builds ~1,140 of them
    per replay. There is nothing to validate and no reason to pay for it.
    """

    match_id: int
    competition_id: int
    utc_date: datetime
    home_team_id: int
    away_team_id: int
    home_goals: int
    away_goals: int


@dataclass
class RatingSnapshot:
    """Both sides' ratings as they stood immediately BEFORE a match.

    These are the at-kickoff values the prediction row stores in elo_home and
    elo_away.
    """

    match_id: int
    home: float
    away: float


@dataclass
class EloState:
    """The result of replaying a match history.

    Attributes:
        ratings: Final rating per (team_id, competition_id). Ratings are keyed
            by competition because Elo points are league-relative and do not
            transfer between them (§2).
        snapshots: One entry per replayed match, in the order they were played.
            Used by the backtest; the live path reads `ratings` instead.
    """

    ratings: dict[tuple[int, int], float] = field(default_factory=dict)
    snapshots: list[RatingSnapshot] = field(default_factory=list)


def expected_score(rating_home: float, rating_away: float) -> float:
    """Expected result for the home side, on the scale win=1, draw=0.5, loss=0.

    HOME_ADVANTAGE is added to the home rating in here, not by the caller, so
    there is exactly one place the bump can be applied or forgotten.

        1 / (1 + 10 ** ((rating_away - (rating_home + HOME_ADVANTAGE)) / 400))

    Args:
        rating_home (float): The home side's current rating, without the bump.
        rating_away (float): The away side's current rating.

    Returns:
        float: A value in [0, 1]. 0.5 means the two sides are level once home
        advantage is accounted for.
    """
    return 1 / (1 + 10 ** ((rating_away - (rating_home + HOME_ADVANTAGE)) / 400))


def goal_difference_multiplier(goal_difference: int) -> float:
    """Scale K by margin of victory, damped at the top end (§6).

    Plain Elo treats 5-0 and 1-0 identically, which throws away real
    information. Undamped scaling inflates strong teams, who win big more
    often. Damped sits between the two.

    Args:
        goal_difference (int): Absolute margin, always >= 1. Draws never reach
            here — the caller skips this when the match was level.

    Returns:
        float: 1.0 for a one-goal margin, rising sub-linearly above that.
    """
    return math.log(goal_difference + 1) / math.log(2)


def seed_rating(ratings: dict[tuple[int, int], float], competition_id: int) -> float:
    """Starting rating for a team appearing in a competition for the first time.

    League average for that competition minus PROMOTED_PENALTY (§9), falling
    back to DEFAULT_RATING when nothing in the competition is rated yet.

    Computed live rather than hardcoded to 1500 for a reason. Elo is zero-sum
    between two teams, so you would expect the league average to hold at 1500
    forever — but relegated teams leave carrying their points and promoted ones
    arrive with a seeded value, so the average drifts. Anchoring new teams to
    the real average keeps the seed meaningful as it does.

    Args:
        ratings (dict): The live rating map, keyed by (team_id, competition_id).
        competition_id (int): The competition the new team is entering.

    Returns:
        float: The seed rating.
    """
    rated = [
        rating for (_, comp_id), rating in ratings.items() if comp_id == competition_id
    ]
    if not rated:
        return DEFAULT_RATING

    return sum(rated) / len(rated) - PROMOTED_PENALTY


def apply_match(
    ratings: dict[tuple[int, int], float], match: MatchResult
) -> RatingSnapshot:
    """Update both sides' ratings in place for one played match.

    Seeds either team if it is not yet in the map, computes the expected result,
    compares it to what happened (1 / 0.5 / 0), and moves both ratings by
    K_FACTOR * goal_difference_multiplier * (actual - expected). Whatever the
    home side gains, the away side loses.

    Args:
        ratings (dict): The live rating map. MUTATED — both teams' entries are
            replaced with their post-match values.
        match (MatchResult): The match to apply.

    Returns:
        RatingSnapshot: The two ratings as they stood BEFORE this update. Taking
        the snapshot here rather than in run() means a full replay yields every
        team's rating at every kickoff as a byproduct.
    """
    home_key = (match.home_team_id, match.competition_id)
    away_key = (match.away_team_id, match.competition_id)

    # Computed once, before either insert. Seeding sequentially would let the
    # first new team move the league average the second one is seeded from,
    # making the pair order-dependent for no reason.
    if home_key not in ratings or away_key not in ratings:
        seed = seed_rating(ratings, match.competition_id)
        ratings.setdefault(home_key, seed)
        ratings.setdefault(away_key, seed)

    rating_home = ratings[home_key]
    rating_away = ratings[away_key]
    snapshot = RatingSnapshot(
        match_id=match.match_id, home=rating_home, away=rating_away
    )

    if match.home_goals > match.away_goals:
        actual = 1.0
    elif match.home_goals < match.away_goals:
        actual = 0.0
    else:
        actual = 0.5

    goal_difference = abs(match.home_goals - match.away_goals)
    # A draw has no margin to scale by, and log(1)/log(2) would zero the update
    # out entirely - a draw between mismatched sides is informative and must
    # still move the ratings.
    multiplier = (
        1.0 if goal_difference == 0 else goal_difference_multiplier(goal_difference)
    )

    change = K_FACTOR * multiplier * (actual - expected_score(rating_home, rating_away))

    # Zero-sum: the points the home side gains are exactly the points the away
    # side loses, which is what keeps the league average stable over a season.
    ratings[home_key] = rating_home + change
    ratings[away_key] = rating_away - change

    return snapshot


def run(matches: Iterable[MatchResult]) -> EloState:
    """Replay a match history in chronological order and return final ratings.

    Sorts by (utc_date, match_id) before walking. The id tie-break is not
    cosmetic: several matches share a kickoff time, and without a deterministic
    order two replays of identical data produce slightly different ratings,
    which would make a backtest unreproducible.

    Args:
        matches (Iterable[MatchResult]): Finished matches only. Fixtures without
            goals have nothing to rate and must be filtered out by the caller.

    Returns:
        EloState: Final ratings, plus one snapshot per replayed match.
    """
    state = EloState()

    for match in sorted(matches, key=lambda m: (m.utc_date, m.match_id)):
        state.snapshots.append(apply_match(state.ratings, match))

    return state
