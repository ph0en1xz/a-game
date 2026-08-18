"""Fixtures, flags and the two client modes for the eval suite.

These are not unit tests. `tests/` proves the code around the model is correct by
feeding it a fake client; this proves the *model* still does what the prompt asks.

    uv run pytest evals --no-cov              replay the saved corpus
    uv run pytest evals --no-cov --live       call Haiku for real
    uv run pytest evals --no-cov --record     call Haiku and rewrite the corpus

    --gateway http://127.0.0.1:4000/v1        route the call through LiteLLM

`--gateway` is the higher-fidelity live mode and needs no API key at all, since
LiteLLM has no master_key configured (see `stores.py`). It keeps `commentary.MODEL`
as the real alias and exercises `drop_params` and the `gpt-4o-mini` fallback the
way production does. It is not usable from GitHub Actions, which cannot reach the
cluster - that is the whole reason the nightly job goes direct to Anthropic.

`--no-cov` is required: the root `addopts` carries `--cov-fail-under=80`, which
an eval run neither can nor should satisfy.

Replay mode does not test the model. It tests this harness and the code path,
which is exactly what makes it safe to run on every pull request for nothing.
"""

import asyncio
import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pytest
from openai import AsyncOpenAI

from app import commentary
from app.commentary import Commentary, write_preview
from app.db_model import Match
from app.engine.poisson import ScoreProbabilities

FIXTURES = Path(__file__).parent / "fixtures"
RECORDED = FIXTURES / "recorded"

# `commentary.MODEL` is a LiteLLM alias and means nothing to Anthropic directly,
# so a live run has to override it. The consequence is that live evals exercise
# the model but never the alias resolution the gateway does in production.
LIVE_MODEL = "claude-haiku-4-5-20251001"
LIVE_BASE_URL = "https://api.anthropic.com/v1/"

# LiteLLM has no master_key configured, so this is never checked - the brain
# sends the same placeholder from `make_llm_client`.
GATEWAY_API_KEY = "sk-noop"


@dataclass(frozen=True)
class Case:
    name: str
    match: Match
    probabilities: ScoreProbabilities
    expected_bet: str


def _load(path: Path) -> Case:
    raw = json.loads(path.read_text())
    return Case(
        name=raw["name"],
        match=Match(
            id=raw["id"],
            season_id=2502,
            competition_id=2021,
            home_team_id=raw["home_team_id"],
            away_team_id=raw["away_team_id"],
            home_team=raw["home_team"],
            away_team=raw["away_team"],
            matchday=raw["matchday"],
            utc_date=datetime.fromisoformat(raw["utc_date"]),
            status="SCHEDULED",
            home_goals=None,
            away_goals=None,
            fulltime_outcome=None,
        ),
        probabilities=ScoreProbabilities(**raw["probabilities"]),
        expected_bet=raw["expected_bet"],
    )


CASES = [_load(p) for p in sorted(FIXTURES.glob("*.json"))]
IDS = [case.name for case in CASES]


# --------------------------------------------------------------------------
# Replay client
# --------------------------------------------------------------------------
# Shaped like `AsyncOpenAI` only where `write_preview` actually touches it. The
# unit tests have their own doubles in `tests/conftest.py`; duplicating the few
# lines here keeps the eval suite from depending on the unit suite.


class _Function:
    def __init__(self, arguments: str):
        self.name = "emit_preview"
        self.arguments = arguments


class _ToolCall:
    def __init__(self, arguments: str):
        self.type = "function"
        self.function = _Function(arguments)


class _Message:
    def __init__(self, arguments: str):
        self.tool_calls = [_ToolCall(arguments)]


class _Choice:
    def __init__(self, arguments: str):
        self.message = _Message(arguments)


class _Completion:
    def __init__(self, arguments: str, model: str):
        self.choices = [_Choice(arguments)]
        self.model = model
        self.usage = None


class _Raw:
    def __init__(self, arguments: str, model: str):
        self._completion = _Completion(arguments, model)
        self.headers = {commentary.MODEL_HEADER: model}

    def parse(self) -> _Completion:
        return self._completion


class ReplayClient:
    """Serves one saved response and records nothing."""

    def __init__(self, arguments: str, model: str):
        raw = _Raw(arguments, model)

        class _WithRaw:
            async def create(self, **kwargs) -> _Raw:
                return raw

        class _Completions:
            with_raw_response = _WithRaw()

        class _Chat:
            completions = _Completions()

        self.chat = _Chat()


# --------------------------------------------------------------------------
# Generation
# --------------------------------------------------------------------------


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--live",
        action="store_true",
        help="call the real model instead of replaying the saved corpus",
    )
    parser.addoption(
        "--record",
        action="store_true",
        help="call the real model and overwrite the saved corpus",
    )
    parser.addoption(
        "--gateway",
        default=None,
        metavar="URL",
        help="route live calls through a LiteLLM base URL instead of Anthropic",
    )


def _generate_live(cases: list[Case], gateway: str | None) -> dict[str, Commentary | None]:
    if gateway:
        base_url, key = gateway, GATEWAY_API_KEY
    else:
        base_url = LIVE_BASE_URL
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            pytest.exit("ANTHROPIC_API_KEY is not set - a live eval run cannot start", 1)
        # Only needed off-gateway: `commentary.MODEL` is a LiteLLM alias.
        commentary.MODEL = LIVE_MODEL

    async def run() -> list[Commentary | None]:
        client = AsyncOpenAI(base_url=base_url, api_key=key)
        try:
            return list(
                await asyncio.gather(
                    *(
                        write_preview(case.match, case.probabilities, client)
                        for case in cases
                    )
                )
            )
        finally:
            await client.close()

    return dict(zip([case.name for case in cases], asyncio.run(run()), strict=True))


def _generate_replay(cases: list[Case]) -> dict[str, Commentary | None]:
    generated: dict[str, Commentary | None] = {}
    for case in cases:
        path = RECORDED / f"{case.name}.json"
        if not path.exists():
            pytest.exit(f"no recorded response for {case.name} - run with --record", 1)
        saved = json.loads(path.read_text())
        client = ReplayClient(json.dumps(saved["arguments"]), saved["model"])
        generated[case.name] = asyncio.run(
            write_preview(case.match, case.probabilities, client)
        )
    return generated


def _record(previews: dict[str, Commentary | None]) -> None:
    for name, preview in previews.items():
        if preview is None:
            continue
        payload = {
            "arguments": {
                "text": preview.text,
                "suggested_bet": preview.suggested_bet,
                "suggested_bet_reason": preview.suggested_bet_reason,
            },
            "model": preview.source_model,
        }
        (RECORDED / f"{name}.json").write_text(json.dumps(payload, indent=2) + "\n")


@pytest.fixture(scope="session")
def previews(request: pytest.FixtureRequest) -> dict[str, Commentary | None]:
    """Every fixture put through `write_preview` once, live or from the corpus.

    Session scoped on purpose: a live run costs real money and both test modules
    read the same output.
    """
    record = request.config.getoption("--record")
    gateway = request.config.getoption("--gateway")
    live = request.config.getoption("--live") or record or bool(gateway)

    generated = _generate_live(CASES, gateway) if live else _generate_replay(CASES)

    if record:
        _record(generated)

    return generated
