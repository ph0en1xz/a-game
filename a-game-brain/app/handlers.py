import logging
from datetime import UTC, datetime

from asyncpg import Pool  # type: ignore
from openai import AsyncOpenAI  # type: ignore
from redis.asyncio import Redis  # type: ignore

from app.commentary import Commentary, write_preview
from app.db import (
    fetch_match_history,
    fetch_match_via_id,
    store_commentary,
    store_prediction,
)
from app.engine import elo, poisson
from app.engine.params import ENGINE_VERSION

log = logging.getLogger("brain.handlers")

CACHE_TTL_SECONDS = 86400

async def process_job(match_id: int, pg: Pool, redis: Redis, client: AsyncOpenAI) -> Commentary | None:
    """Process one "match changed" event from the queue.

    Args:
        match_id (int): football-data's match id, as published by the worker.
        pg (asyncpg.Pool): The PostgreSQL connection pool.
        redis (redis.Redis): The Redis client.
        client (AsyncOpenAI): The LiteLLM-backed client used to write previews.

    Returns:
        Commentary | None: The generated preview, or None if the match was not
        found, the model returned nothing usable, or the job raised.
    """
    try:
        log.info("Processing match %d", match_id)

        match = await fetch_match_via_id(match_id, pg)
        if match is None:
            return match

        # The training step. Nothing is persisted between runs - both models are
        # rebuilt from the match history every time, which takes milliseconds at
        # three seasons and is always current by construction.
        history = await fetch_match_history(match.competition_id, pg)
        ratings = elo.run(history).ratings
        strengths = poisson.fit(history, datetime.now(UTC))

        lambda_home, lambda_away = poisson.expected_goals(
            strengths, match.home_team_id, match.away_team_id, match.competition_id
        )
        probabilities = poisson.score_probabilities(lambda_home, lambda_away)

        # Elo is context and a cross-check, never the probability source (§14).
        # A side with no rated history falls back to the same seed the rating
        # loop would have given it.
        seed = elo.seed_rating(ratings, match.competition_id)
        elo_home = ratings.get((match.home_team_id, match.competition_id), seed)
        elo_away = ratings.get((match.away_team_id, match.competition_id), seed)

        preview: Commentary | None = await write_preview(match, probabilities, client)
        if preview is None:
            log.warning("no preview for match %d", match_id)

        # One transaction: a fixture never ends up with prose and no numbers.
        async with pg.acquire() as conn, conn.transaction():
            await store_prediction(
               conn, 
               match.id, 
               ENGINE_VERSION,
               probabilities, 
               elo_home, 
               elo_away
            )

            if preview is not None:
                await store_commentary(
                    conn,
                    match.id,
                    preview.source_model,
                    preview.text,
                    preview.suggested_bet,
                    preview.suggested_bet_reason,
                )

        await redis.set(
            f"match_id:{match_id}",
            preview.text,
            ex=CACHE_TTL_SECONDS
        )

        log.info(
            "processed match %d (status=%s): %.2f/%.2f/%.2f, lambdas %.2f/%.2f",
            match_id,
            match.status,
            probabilities.prob_home,
            probabilities.prob_draw,
            probabilities.prob_away,
            lambda_home,
            lambda_away,
        )

        return preview
    
    except Exception:  # broad on purpose - one bad message must not kill the consumer
        log.exception("Error processing job")
        return None