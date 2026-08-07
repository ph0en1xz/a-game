import logging
from textwrap import dedent

from openai import AsyncOpenAI, OpenAIError  # type: ignore
from openai.types.chat import (  # type: ignore
    ChatCompletionMessageParam,
    ChatCompletionNamedToolChoiceParam,
    ChatCompletionToolParam,
)
from pydantic import BaseModel, Field, ValidationError

from app.db_model import Match

log = logging.getLogger("brain.commentary")


class Commentary(BaseModel):
    """Validated preview text returned by the model."""

    text: str = Field(min_length=40, max_length=600)


MODEL = "claude-haiku"

# Pinned to 0 by the CI eval harness so runs are reproducible.
TEMPERATURE = 0.7

SYSTEM_PROMPT = dedent("""
    You are a football writer producing short match previews.

    Rules:
    - Use only the facts given in the user message. Do not add statistics, form
      records, league positions, injuries, transfers, or historical results.
    - Do not predict a scoreline or state who will win as fact.
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
            },
            "required": ["text"],
        },
    },
}

TOOL_CHOICE: ChatCompletionNamedToolChoiceParam = {
    "type": "function",
    "function": {"name": "emit_preview"},
}


def _user_prompt(match: Match) -> str:
    return dedent(f"""
        Home team: {match.home_team}
        Away team: {match.away_team}
        Kickoff (UTC): {match.utc_date.strftime("%A %d %B %Y, %H:%M")}
        Matchday: {match.matchday if match.matchday is not None else "unknown"}
    """).strip()


async def write_preview(match: Match, client: AsyncOpenAI) -> Commentary | None:
    """Write a preview of the match based on the predicted commentary.

    Args:
        match (Match): The match record.
        client (AsyncOpenAI): The OpenAI client.
    Returns:
        Commentary | None: The commentary record, or None if the commentary could not be generated.
    """

    log.info("Generating preview for match: %s vs %s", match.home_team, match.away_team)

    messages: list[ChatCompletionMessageParam] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": _user_prompt(match)},
    ]

    try:
        response = await client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=[PREVIEW_TOOL],
            tool_choice=TOOL_CHOICE,
            temperature=TEMPERATURE,
            max_tokens=400,
        )
    except OpenAIError as e:
        log.error("Error generating preview for match %d: %s", match.id, str(e))
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

    log.info("Generated preview for match %d (%d chars)", match.id, len(commentary.text))
    return commentary
