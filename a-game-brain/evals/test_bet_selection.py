"""How often does the model name the market the engine actually flags?

The one check here that is not a matter of taste. `SYSTEM_PROMPT` asks for the
largest departure from the league baseline and `_user_prompt` supplies those
baselines, so the intended answer is computable - see `evals/baseline.py`.
"""

import pytest

from app.commentary import Commentary
from evals.baseline import acceptable, expected_bet
from evals.conftest import CASES, IDS, Case

THRESHOLD = 0.8


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_the_fixture_agrees_with_the_scorer(case: Case) -> None:
    """Guards the corpus itself. A hand-written answer that drifted out of step
    with `departures()` would quietly score the model against the wrong target."""
    assert case.expected_bet == expected_bet(case.probabilities)


def test_bet_selection_accuracy(previews: dict[str, Commentary | None]) -> None:
    rows = []
    hits = 0

    for case in CASES:
        preview = previews[case.name]
        chosen = preview.suggested_bet if preview else "<no preview>"
        allowed = acceptable(case.probabilities)
        ok = chosen in allowed
        hits += ok
        rows.append(
            f"  {'ok  ' if ok else 'MISS'} {case.name:<24} "
            f"chose {chosen:<26} wanted {' / '.join(sorted(allowed))}"
        )

    table = "\n".join(
        [f"\nbet selection: {hits}/{len(CASES)}", *rows]
    )
    print(table)

    assert hits / len(CASES) >= THRESHOLD, table
