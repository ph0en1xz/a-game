import logging
from textwrap import dedent

from openai import AsyncOpenAI, OpenAIError # type: ignore
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
    - Reply with a single JSON object and nothing else: {"text": "<the preview>"}
""").strip()


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

    try:
        response = await client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _user_prompt(match)},
            ],
            response_format={"type": "json_object"},
            temperature=TEMPERATURE,
            max_tokens=400,
        )
    except OpenAIError as e:
        log.error("Error generating preview for match %d: %s", match.id, str(e))
        return None

    content = response.choices[0].message.content
    if not content or content.strip() == "":
        log.error("No content returned for match %d", match.id)
        return None

    try:
        commentary = Commentary.model_validate_json(content)
    except ValidationError as e:
        log.error("Invalid JSON returned for match %d: %s", match.id, str(e))
        return None

    log.info(
        "Generated preview for match %d (%d chars)", match.id, len(commentary.text)
    )
    return commentary
