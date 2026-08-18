import logging
from textwrap import dedent

from openai import AsyncOpenAI, OpenAIError  # type: ignore
from openai.types.chat import (  # type: ignore
    ChatCompletionMessageParam,
    ChatCompletionNamedToolChoiceParam,
    ChatCompletionToolParam,
)
from pydantic import BaseModel, Field, ValidationError, field_validator

from app.db_model import Match
from app.engine.poisson import ScoreProbabilities

log = logging.getLogger("brain.commentary")



# The only markets the engine actually computes. An enum rather than free text
# because the model will otherwise invent markets we have no number for -
# "Manchester City -1 handicap" is not something this engine can support.
SUGGESTED_BETS = [
    "Home win",
    "Draw",
    "Away win",
    "Over 2.5 goals",
    "Under 2.5 goals",
    "Both teams to score",
    "Both teams to score - no",
]


class Commentary(BaseModel):
    """Validated preview text returned by the model."""

    text: str = Field(min_length=40, max_length=600)

    # Literal-typed so a market outside the list fails validation here too, not
    # only in the tool schema - JSON Schema enums are advisory to the model.
    suggested_bet: str = Field(default="")
    suggested_bet_reason: str = Field(default="", max_length=200)

    source_model: str = Field(
        default="",
        description="The model that produced the preview. This is the model's name, not an alias, so a fallback is recorded honestly.",
    )

    @field_validator("suggested_bet")
    @classmethod
    def known_market(cls, value: str) -> str:
        if value and value not in SUGGESTED_BETS:
            raise ValueError(f"unknown market: {value!r}")
        return value

MODEL = "claude-haiku"

TEMPERATURE = 0.7

REQUEST_TIMEOUT = 30.0

MODEL_HEADER = "x-litellm-model-name"

SYSTEM_PROMPT = dedent("""
    You are a football writer producing short match previews.

    Rules:
    - Use ONLY the facts and numbers given in the user message. Everything you
      know about these clubs from elsewhere is off limits - including the
      stadium or city, nicknames, rivalries, history, honours, form, league
      position, injuries and transfers. Name each club only as the user message
      names it: "Sheffield United", never "the Blades". If a detail is not in
      the user message, it does not exist.
    - The percentages given are a statistical model's output. Quote them as
      given, never recompute or round them differently, and never invent a
      number that is not in the list. Do not combine, total or average them
      either - two expected-goal figures are two figures, not one sum.
    - Call them probabilities, never odds. Odds are a bookmaker's price and we
      have none; "74% odds" is wrong and is the exact confusion to avoid.
    - Report them as probabilities, not certainties. "The model makes City
      slight favourites at 39%" is right; "City will win" is not.
    - Pick the one market where the model departs most from the league baseline
      and name it in suggested_bet. This is the model's strongest call, NOT a
      value bet - we have no bookmaker odds to compare against, so never claim
      a price is good, an edge exists, or a bet is profitable.
    - Write 2-4 sentences of plain prose. No markdown, no headings, no lists.
    - Return your answer by calling emit_preview.
""").strip()


# Not a real function - nothing ever executes it. Declaring it as a tool and
# forcing tool_choice is how Anthropic does structured output, so the answer
# comes back as JSON in its own field instead of free text the model might
# wrap in a markdown fence.
#
# response_format is not an option here. LiteLLM supports it for Anthropic
# only as {"type": "json_schema"} - never json_object - and only on Sonnet 4.5
# and Opus 4.1+, so it is doubly unsupported on Haiku. With drop_params: true
# in the gateway config it was silently discarded, leaving nothing enforcing
# the shape; the model returned its JSON inside a ```json fence and pydantic
# rejected it. tools/tool_choice are supported for Anthropic across the board.
# https://docs.litellm.ai/docs/providers/anthropic
PREVIEW_TOOL: ChatCompletionToolParam = {
    "type": "function",
    "function": {
        "name": "emit_preview",
        "description": "Return the finished match preview.",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "2-4 sentences of plain prose. No markdown.",
                    "minLength": 40,
                    "maxLength": 600,
                },
                "suggested_bet": {
                    "type": "string",
                    "description": (
                        "The market where the model departs most from the "
                        "league baseline, e.g. 'Over 2.5 goals' or 'Draw'."
                    ),
                    "enum": SUGGESTED_BETS,
                },
                "suggested_bet_reason": {
                    "type": "string",
                    "description": (
                        "One sentence citing the model percentage and the "
                        "baseline it beats. No claims about odds or value."
                    ),
                    "minLength": 20,
                    "maxLength": 200,
                },
            },
            "required": ["text", "suggested_bet", "suggested_bet_reason"],
        },
    },
}

TOOL_CHOICE: ChatCompletionNamedToolChoiceParam = {
    "type": "function",
    "function": {"name": "emit_preview"},
}


def _user_prompt(match: Match, probabilities: ScoreProbabilities) -> str:
    """Assemble the only facts the model is allowed to use.

    Percentages rather than decimals, because models handle "39%" more reliably
    than "0.3914" and the extra precision is meaningless to a reader anyway.
    """
    scores = ", ".join(
        f"{s['home']}-{s['away']} ({float(s['prob']):.0%})"
        for s in probabilities.most_likely_scores
    )

    return dedent(f"""
        Home team: {match.home_team}
        Away team: {match.away_team}
        Kickoff (UTC): {match.utc_date.strftime("%A %d %B %Y, %H:%M")}
        Matchday: {match.matchday if match.matchday is not None else "unknown"}

        Our model's numbers for this fixture:
        - Home win: {probabilities.prob_home:.0%}
        - Draw: {probabilities.prob_draw:.0%}
        - Away win: {probabilities.prob_away:.0%}
        - Over 2.5 goals: {probabilities.over_2_5:.0%}
        - Under 2.5 goals: {1 - probabilities.over_2_5:.0%}
        - Both teams to score: {probabilities.btts:.0%}
        - Both teams to score - no: {1 - probabilities.btts:.0%}
        - Expected goals: {probabilities.lambda_home:.2f} home, {probabilities.lambda_away:.2f} away
        - Most likely scorelines: {scores}

        League baselines for comparison: home win 45%, draw 27%, away win 28%,
        over 2.5 goals 52%, under 2.5 goals 48%, both teams to score 48%,
        both teams to score - no 52%.
    """).strip()


async def write_preview(
    match: Match, probabilities: ScoreProbabilities, client: AsyncOpenAI
) -> Commentary | None:
    """Write a preview of the match, phrased around the model's own numbers.

    The LLM phrases the numbers, it never invents them (ADR 0008) - which is
    why the probabilities are an argument rather than something this function
    could ever compute.

    Args:
        match (Match): The match record.
        probabilities (ScoreProbabilities): The engine's output for this fixture.
        client (AsyncOpenAI): The OpenAI client.
    Returns:
        Commentary | None: The commentary record, or None if the commentary could not be generated.
    """

    log.info("Generating preview for match: %s vs %s", match.home_team, match.away_team)

    messages: list[ChatCompletionMessageParam] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _user_prompt(match, probabilities)},
    ]

    try:
        raw = await client.chat.completions.with_raw_response.create(
            model=MODEL,
            messages=messages,
            tools=[PREVIEW_TOOL],
            tool_choice=TOOL_CHOICE,
            temperature=TEMPERATURE,
            max_tokens=400,
            timeout=REQUEST_TIMEOUT,
        )
        response = raw.parse()
    except OpenAIError as e:
        log.error("Error generating preview for match %d: %s", match.id, str(e))
        return None

    if not response.choices:
        log.error("No choices returned for match %d", match.id)
        return None

    tool_calls = response.choices[0].message.tool_calls
    if not tool_calls:
        log.error("No tool call returned for match %d", match.id)
        return None

    tool_call = tool_calls[0]
    if tool_call.type != "function":
        log.error("Unexpected tool call type for match %d: %s", match.id, tool_call.type)
        return None
    
    arguments = tool_call.function.arguments
    try:
        commentary = Commentary.model_validate_json(arguments)
    except ValidationError as e:
        log.error("Invalid JSON returned for match %d: %s", match.id, str(e))
        return None

    commentary.source_model = raw.headers.get(MODEL_HEADER) or response.model

    if response.usage:
        log.info(
            "Preview for match %d: %d chars, %d in / %d out tokens, model=%s",
            match.id,
            len(commentary.text),
            response.usage.prompt_tokens,
            response.usage.completion_tokens,
            commentary.source_model,
        )

    return commentary