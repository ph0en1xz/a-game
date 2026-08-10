"""Preview generation (`app/commentary.py`).

The LLM is the least reliable component in the system, so most of this file is
about what happens when it misbehaves: no choices, no tool call, malformed JSON, a
market nobody computes, a timeout. Every one of those must return None and let the
caller store the numbers anyway — a fixture with prose and no probabilities is a
broken row, but numbers without prose is merely a quiet one.

The other half is provenance. `source_model` has to be the model that actually
answered, not the alias that was asked for, or a fallback to the secondary
provider is invisible in the database.
"""

import json

import pytest
from pydantic import ValidationError

from app import commentary
from app.commentary import SUGGESTED_BETS, Commentary, write_preview
from app.engine import poisson
from tests.conftest import (
    FakeCompletion,
    FakeLLMClient,
    FakeRawResponse,
    FakeToolCall,
    tool_arguments,
)

PROBS = poisson.score_probabilities(1.31, 1.21)
RESOLVED = "anthropic/claude-haiku-4-5-20251001"


def raw(arguments: str | None = None, *, headers=None, model="claude-haiku", **kw):
    tool_calls = [FakeToolCall(arguments or tool_arguments())] if arguments != "" else []
    return FakeRawResponse(
        FakeCompletion(tool_calls, model=model, **kw),
        headers=headers if headers is not None else {"x-litellm-model-name": RESOLVED},
    )


# --------------------------------------------------------------------------
# The Commentary model
# --------------------------------------------------------------------------


def test_a_valid_payload_parses():
    parsed = Commentary.model_validate_json(tool_arguments())

    assert parsed.suggested_bet == "Over 2.5 goals"
    assert parsed.source_model == ""


def test_source_model_defaults_to_empty():
    """It is filled in after parsing, from the gateway's response header.

    Declaring it without a default would make it required, and every single
    validation would fail — the model never sends it, so `model_validate_json`
    would raise on a perfectly good response and every preview would come back
    None.
    """
    parsed = Commentary.model_validate_json(
        json.dumps({"text": "x" * 60, "suggested_bet": "Draw", "suggested_bet_reason": "r"})
    )

    assert parsed.source_model == ""


def test_text_below_the_floor_is_rejected():
    with pytest.raises(ValidationError):
        Commentary.model_validate_json(json.dumps({"text": "Too short."}))


def test_text_above_the_ceiling_is_rejected():
    with pytest.raises(ValidationError):
        Commentary.model_validate_json(json.dumps({"text": "x" * 601}))


@pytest.mark.parametrize("market", SUGGESTED_BETS)
def test_every_listed_market_validates(market):
    parsed = Commentary.model_validate_json(tool_arguments(suggested_bet=market))

    assert parsed.suggested_bet == market


def test_an_invented_market_is_rejected():
    """JSON Schema enums are advisory to the model — it will happily return
    "Manchester City -1 handicap", which this engine has no number for. The
    validator is what actually enforces the closed set."""
    with pytest.raises(ValidationError):
        Commentary.model_validate_json(
            tool_arguments(suggested_bet="Manchester City -1 handicap")
        )


def test_an_empty_market_is_allowed():
    """The model declining to pick one is a valid answer; an invented one is not."""
    parsed = Commentary.model_validate_json(tool_arguments(suggested_bet=""))

    assert parsed.suggested_bet == ""


def test_an_overlong_reason_is_rejected():
    with pytest.raises(ValidationError):
        Commentary.model_validate_json(tool_arguments(suggested_bet_reason="x" * 201))


# --------------------------------------------------------------------------
# The prompt
# --------------------------------------------------------------------------


def test_the_prompt_carries_the_model_numbers(match):
    prompt = commentary._user_prompt(match, PROBS)

    assert "Manchester City FC" in prompt
    assert "Arsenal FC" in prompt
    assert "39%" in prompt
    assert "Over 2.5 goals" in prompt


def test_the_prompt_carries_the_league_baselines(match):
    """The suggested bet is defined relative to these. Without them in the prompt
    the model has nothing to compare against and will invent a baseline."""
    prompt = commentary._user_prompt(match, PROBS)

    assert "baseline" in prompt.lower()
    assert "45%" in prompt


def test_the_prompt_handles_a_missing_matchday(match):
    """matchday is null for some cup stages — rendering `None` into the prompt
    would have the model writing about matchday None."""
    prompt = commentary._user_prompt(match.model_copy(update={"matchday": None}), PROBS)

    assert "unknown" in prompt


def test_percentages_not_decimals(match):
    """Models handle "39%" far more reliably than "0.3914", and the extra precision
    means nothing to a reader."""
    prompt = commentary._user_prompt(match, PROBS)

    assert "0.3914" not in prompt


def test_the_system_prompt_forbids_outside_knowledge():
    assert "ONLY" in commentary.SYSTEM_PROMPT
    assert "stadium" in commentary.SYSTEM_PROMPT


def test_the_system_prompt_forbids_value_claims():
    """No odds feed exists, so any claim about a price being good is fabrication."""
    lowered = commentary.SYSTEM_PROMPT.lower()

    assert "never claim" in lowered
    assert "value bet" in lowered


def test_the_tool_schema_pins_the_market_list():
    """Belt and braces with the validator — the enum steers the model, the
    validator catches it when the steering fails."""
    schema = commentary.PREVIEW_TOOL["function"]["parameters"]

    assert schema["properties"]["suggested_bet"]["enum"] == SUGGESTED_BETS
    assert set(schema["required"]) == {"text", "suggested_bet", "suggested_bet_reason"}


# --------------------------------------------------------------------------
# write_preview — the happy path
# --------------------------------------------------------------------------


async def test_a_good_response_becomes_a_commentary(match):
    client = FakeLLMClient(raw=raw())

    preview = await write_preview(match, PROBS, client)

    assert preview is not None
    assert preview.suggested_bet == "Over 2.5 goals"
    assert len(preview.text) >= 40


async def test_the_request_forces_the_tool_call(match):
    """Anthropic structured output via forced tools.

    `response_format` is unsupported for Haiku through LiteLLM and, with
    drop_params on, was silently discarded — leaving nothing enforcing the shape
    and the model wrapping its JSON in a markdown fence.
    """
    client = FakeLLMClient(raw=raw())

    await write_preview(match, PROBS, client)

    sent = client.calls[0]
    assert sent["tools"] == [commentary.PREVIEW_TOOL]
    assert sent["tool_choice"] == commentary.TOOL_CHOICE
    assert sent["model"] == commentary.MODEL


async def test_the_request_carries_a_timeout(match):
    """No timeout means a hung gateway blocks the consumer forever, and with
    prefetch_count=1 that stalls the entire queue."""
    client = FakeLLMClient(raw=raw())

    await write_preview(match, PROBS, client)

    assert client.calls[0]["timeout"] == commentary.REQUEST_TIMEOUT


async def test_both_prompts_are_sent(match):
    client = FakeLLMClient(raw=raw())

    await write_preview(match, PROBS, client)

    messages = client.calls[0]["messages"]
    assert [m["role"] for m in messages] == ["system", "user"]
    assert "Manchester City FC" in messages[1]["content"]


# --------------------------------------------------------------------------
# write_preview — provenance
# --------------------------------------------------------------------------


async def test_source_model_comes_from_the_gateway_header(match):
    """LiteLLM echoes the *requested* alias in the response body, so the body's
    `model` field says "claude-haiku" whichever provider actually answered. The
    header is the only place the resolved model appears."""
    client = FakeLLMClient(raw=raw(model="claude-haiku"))

    preview = await write_preview(match, PROBS, client)

    assert preview.source_model == RESOLVED


async def test_source_model_falls_back_to_the_body(match):
    """If the gateway ever stops sending the header, record something rather than
    an empty string."""
    client = FakeLLMClient(raw=raw(headers={}, model="gpt-4o-mini"))

    preview = await write_preview(match, PROBS, client)

    assert preview.source_model == "gpt-4o-mini"


async def test_a_fallback_provider_is_recorded_honestly(match):
    """The whole point: when Haiku is down and gpt-4o-mini answers, the row must
    not claim Haiku wrote it."""
    client = FakeLLMClient(
        raw=raw(headers={"x-litellm-model-name": "openai/gpt-4o-mini"})
    )

    preview = await write_preview(match, PROBS, client)

    assert preview.source_model == "openai/gpt-4o-mini"


# --------------------------------------------------------------------------
# write_preview — failure modes, all of which return None
# --------------------------------------------------------------------------


async def test_an_api_error_returns_none(match):
    from openai import APIConnectionError

    client = FakeLLMClient(error=APIConnectionError(request=None))

    assert await write_preview(match, PROBS, client) is None


async def test_a_timeout_returns_none(match):
    from openai import APITimeoutError

    client = FakeLLMClient(error=APITimeoutError(request=None))

    assert await write_preview(match, PROBS, client) is None


async def test_no_choices_returns_none(match):
    client = FakeLLMClient(raw=FakeRawResponse(FakeCompletion(None)))

    assert await write_preview(match, PROBS, client) is None


async def test_no_tool_call_returns_none(match):
    """The model answered in prose instead of calling the tool."""
    client = FakeLLMClient(raw=raw(""))

    assert await write_preview(match, PROBS, client) is None


async def test_a_non_function_tool_call_returns_none(match):
    """Providers are adding other tool types. Reading `.function.arguments` off
    one would be an AttributeError inside the consumer, so it's checked first."""
    completion = FakeCompletion([FakeToolCall(tool_arguments(), call_type="custom")])
    client = FakeLLMClient(raw=FakeRawResponse(completion))

    assert await write_preview(match, PROBS, client) is None


async def test_malformed_json_returns_none(match):
    client = FakeLLMClient(raw=raw("{not json at all"))

    assert await write_preview(match, PROBS, client) is None


async def test_an_invented_market_returns_none(match):
    """Validation failure is a dropped preview, not a stored lie."""
    client = FakeLLMClient(raw=raw(tool_arguments(suggested_bet="Correct score 3-1")))

    assert await write_preview(match, PROBS, client) is None


async def test_text_too_short_returns_none(match):
    client = FakeLLMClient(raw=raw(json.dumps({"text": "Nope."})))

    assert await write_preview(match, PROBS, client) is None


async def test_a_missing_usage_block_is_not_fatal(match):
    """Usage is logging only — its absence must not cost a valid preview."""
    client = FakeLLMClient(raw=raw(usage=False))

    preview = await write_preview(match, PROBS, client)

    assert preview is not None
