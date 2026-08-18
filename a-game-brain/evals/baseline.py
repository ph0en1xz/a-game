"""The `suggested_bet` the model is supposed to pick, computed in Python.

`SYSTEM_PROMPT` tells the model to name the market where the engine departs most
from the league baseline, and `_user_prompt` hands it those baselines as fixed
numbers. That makes the intended answer deterministic, which is the only reason
scoring the model's choice means anything at all.

Rounding here is deliberate. `_user_prompt` formats every probability with
`:.0%`, so the model is shown `58%` and never `0.5814`. Scoring against the
unrounded float would invent disagreements the model had no way to avoid.
"""

from app.engine.poisson import ScoreProbabilities

# Must stay in step with the baselines literal at the end of `_user_prompt`.
BASELINES: dict[str, float] = {
    "Home win": 0.45,
    "Draw": 0.27,
    "Away win": 0.28,
    "Over 2.5 goals": 0.52,
    "Both teams to score": 0.48,
}

# Only two markets have a nameable other side. A home win landing under baseline
# is not "not a home win" - the draw and away numbers already carry that.
INVERSE: dict[str, str] = {
    "Over 2.5 goals": "Under 2.5 goals",
    "Both teams to score": "Both teams to score - no",
}

TOLERANCE = 0.02


def departures(probabilities: ScoreProbabilities) -> list[tuple[str, float]]:
    """Every nameable market, ranked by distance from its baseline."""
    shown = {
        "Home win": round(probabilities.prob_home, 2),
        "Draw": round(probabilities.prob_draw, 2),
        "Away win": round(probabilities.prob_away, 2),
        "Over 2.5 goals": round(probabilities.over_2_5, 2),
        "Both teams to score": round(probabilities.btts, 2),
    }

    ranked: list[tuple[str, float]] = []
    for market, value in shown.items():
        delta = value - BASELINES[market]
        if delta < 0:
            if market not in INVERSE:
                continue
            market = INVERSE[market]
        ranked.append((market, round(abs(delta), 4)))

    return sorted(ranked, key=lambda row: -row[1])


def expected_bet(probabilities: ScoreProbabilities) -> str:
    """The single market the engine departs furthest on."""
    return departures(probabilities)[0][0]


def acceptable(probabilities: ScoreProbabilities) -> set[str]:
    """The winner, plus anything close enough that picking it is not an error.

    Without this the score punishes the model for calling a near tie, which says
    nothing about whether the prompt still works.
    """
    ranked = departures(probabilities)
    best = ranked[0][1]
    return {market for market, delta in ranked if best - delta <= TOLERANCE}
