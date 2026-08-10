"""Job orchestration (`app/handlers.py`).

`process_job` is where the whole service comes together: load history, run both
models, ask the LLM, write both rows in one transaction. These tests are about
the sequencing and the failure behaviour, not the maths — the engine has its own
files for that.

Two properties matter most. A fixture must never end up with prose and no numbers.
And one bad message must never kill the consumer, because with `prefetch_count=1`
a dead consumer is a stalled queue.
"""

import pytest

from app import handlers
from app.commentary import Commentary
from app.engine.params import ENGINE_VERSION
from tests.conftest import make_match, result


def preview(**overrides) -> Commentary:
    base = {
        "text": "x" * 80,
        "suggested_bet": "Over 2.5 goals",
        "suggested_bet_reason": "58% against a 52% baseline.",
        "source_model": "anthropic/claude-haiku-4-5-20251001",
    }
    base.update(overrides)
    return Commentary(**base)


# Sentinel rather than `preview()` as a default argument: a mutable default is
# built once at import and shared by every test that doesn't override it.
_UNSET = object()


@pytest.fixture
def wired(monkeypatch):
    """Patch every collaborator `process_job` reaches for and record the calls."""

    def _configure(
        *,
        match=None,
        history=None,
        preview_result=_UNSET,
        preview_error=None,
        store_prediction_error=None,
    ):
        if preview_result is _UNSET:
            preview_result = preview()
        calls: list[str] = []
        captured: dict = {}

        async def fake_fetch_match(match_id, pg):
            calls.append("fetch_match")
            return match

        async def fake_history(competition_id, pg, before=None):
            calls.append("fetch_history")
            captured["competition_id"] = competition_id
            return list(history if history is not None else [])

        async def fake_write_preview(m, probabilities, client):
            calls.append("write_preview")
            captured["probabilities"] = probabilities
            if preview_error is not None:
                raise preview_error
            return preview_result

        async def fake_store_prediction(conn, match_id, version, probs, home, away):
            calls.append("store_prediction")
            captured["prediction"] = (match_id, version, probs, home, away)
            if store_prediction_error is not None:
                raise store_prediction_error

        async def fake_store_commentary(conn, match_id, source_model, text, bet="", reason=""):
            calls.append("store_commentary")
            captured["commentary"] = (match_id, source_model, text, bet, reason)

        monkeypatch.setattr(handlers, "fetch_match_via_id", fake_fetch_match)
        monkeypatch.setattr(handlers, "fetch_match_history", fake_history)
        monkeypatch.setattr(handlers, "write_preview", fake_write_preview)
        monkeypatch.setattr(handlers, "store_prediction", fake_store_prediction)
        monkeypatch.setattr(handlers, "store_commentary", fake_store_commentary)

        return calls, captured

    return _configure


@pytest.fixture
def history():
    return [
        result(1, 1, 2, 3, 0, days_ago=90),
        result(2, 2, 3, 2, 1, days_ago=80),
        result(3, 3, 1, 0, 2, days_ago=70),
        result(4, 65, 57, 2, 1, days_ago=30),
        result(5, 57, 65, 1, 1, days_ago=20),
    ]


async def test_a_full_job_writes_both_rows(wired, history, pool, fake_redis, llm_client):
    calls, captured = wired(match=make_match(), history=history)

    returned = await handlers.process_job(538107, pool, fake_redis, llm_client)

    assert returned is not None
    assert "store_prediction" in calls
    assert "store_commentary" in calls
    assert captured["prediction"][1] == ENGINE_VERSION


async def test_an_unknown_match_does_nothing(wired, pool, fake_redis, llm_client):
    """The worker can publish an id the brain can't resolve. That's a no-op, not
    an error."""
    calls, _ = wired(match=None)

    assert await handlers.process_job(999, pool, fake_redis, llm_client) is None
    assert calls == ["fetch_match"]


async def test_the_history_is_scoped_to_the_fixtures_competition(
    wired, history, pool, fake_redis, llm_client
):
    _, captured = wired(match=make_match(competition_id=2021), history=history)

    await handlers.process_job(538107, pool, fake_redis, llm_client)

    assert captured["competition_id"] == 2021


async def test_the_last_match_is_cached(wired, history, pool, fake_redis, llm_client):
    wired(match=make_match(), history=history)

    await handlers.process_job(538107, pool, fake_redis, llm_client)

    assert fake_redis.store["brain:last_match"] == 538107
    assert fake_redis.expiries["brain:last_match"] == handlers.SEVEN_DAYS


async def test_the_probabilities_reach_both_the_llm_and_the_writer(
    wired, history, pool, fake_redis, llm_client
):
    """The LLM phrases the numbers, it never computes them — same object goes to
    the prompt and to Postgres."""
    _, captured = wired(match=make_match(), history=history)

    await handlers.process_job(538107, pool, fake_redis, llm_client)

    assert captured["probabilities"] is captured["prediction"][2]


async def test_the_probabilities_sum_to_one(wired, history, pool, fake_redis, llm_client):
    _, captured = wired(match=make_match(), history=history)

    await handlers.process_job(538107, pool, fake_redis, llm_client)

    probs = captured["probabilities"]
    assert probs.prob_home + probs.prob_draw + probs.prob_away == pytest.approx(1.0)


async def test_elo_context_is_stored(wired, history, pool, fake_redis, llm_client):
    _, captured = wired(match=make_match(), history=history)

    await handlers.process_job(538107, pool, fake_redis, llm_client)

    _, _, _, elo_home, elo_away = captured["prediction"]
    assert elo_home > 0 and elo_away > 0


async def test_an_unrated_side_falls_back_to_the_seed(
    wired, pool, fake_redis, llm_client
):
    """A fixture involving a team with no rated history still needs two numbers.

    The fallback has to be the same seed the rating loop would have used, or the
    stored Elo is inconsistent with the model that produced the probabilities.
    """
    other_teams_only = [result(1, 1, 2, 3, 0, days_ago=90)]
    _, captured = wired(match=make_match(), history=other_teams_only)

    await handlers.process_job(538107, pool, fake_redis, llm_client)

    _, _, _, elo_home, elo_away = captured["prediction"]
    assert elo_home == elo_away


async def test_the_suggested_bet_is_persisted(wired, history, pool, fake_redis, llm_client):
    _, captured = wired(match=make_match(), history=history)

    await handlers.process_job(538107, pool, fake_redis, llm_client)

    _, _, _, bet, reason = captured["commentary"]
    assert bet == "Over 2.5 goals"
    assert reason.startswith("58%")


async def test_the_resolved_model_is_persisted(wired, history, pool, fake_redis, llm_client):
    _, captured = wired(match=make_match(), history=history)

    await handlers.process_job(538107, pool, fake_redis, llm_client)

    assert captured["commentary"][1] == "anthropic/claude-haiku-4-5-20251001"


async def test_numbers_are_stored_even_without_a_preview(
    wired, history, pool, fake_redis, llm_client
):
    """LiteLLM being down must not cost the prediction. The maths is the product;
    the prose is the garnish.
    """
    calls, _ = wired(match=make_match(), history=history, preview_result=None)

    returned = await handlers.process_job(538107, pool, fake_redis, llm_client)

    assert returned is None
    assert "store_prediction" in calls
    assert "store_commentary" not in calls


async def test_both_writes_share_one_connection(
    wired, history, pool, fake_redis, llm_client
):
    """One acquire, one transaction — a fixture never gets prose without numbers."""
    wired(match=make_match(), history=history)

    await handlers.process_job(538107, pool, fake_redis, llm_client)

    assert pool.acquired == 1


async def test_a_write_failure_does_not_kill_the_consumer(
    wired, history, pool, fake_redis, llm_client
):
    """The broad except is deliberate: one bad message must not take down the
    consumer, because prefetch_count=1 means a dead consumer is a stalled queue.
    """
    wired(
        match=make_match(),
        history=history,
        store_prediction_error=RuntimeError("postgres gone"),
    )

    assert await handlers.process_job(538107, pool, fake_redis, llm_client) is None


async def test_an_llm_exception_does_not_kill_the_consumer(
    wired, history, pool, fake_redis, llm_client
):
    wired(match=make_match(), history=history, preview_error=RuntimeError("gateway"))

    assert await handlers.process_job(538107, pool, fake_redis, llm_client) is None


async def test_an_empty_history_still_produces_a_prediction(
    wired, pool, fake_redis, llm_client
):
    """Day one of a new competition: both models fall back to league average and
    the fixture still gets numbers rather than a crash."""
    _, captured = wired(match=make_match(), history=[])

    await handlers.process_job(538107, pool, fake_redis, llm_client)

    assert "prediction" in captured
