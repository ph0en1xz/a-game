import logging

from datetime import date

import asyncpg

log = logging.getLogger("worker.db")

async def sync_matches_per_competition(pool: asyncpg.Pool, matches: list[list[dict]]) -> int:
    UPSERT = """
        
    """

async def get_league_codes(pool: asyncpg.Pool) -> list[str]:
    QUERY = """
        SELECT code
        FROM a_game.competition
        WHERE enabled
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(QUERY)
        log.info("League codes fetched")

    codes = [row["code"] for row in rows]
    log.info("Fetched %d enabled league codes", len(codes))
    return codes

async def sync_competitions(pool: asyncpg.Pool, competitions: list[dict]) -> int:
    UPSERT_COMPETITION = """
        INSERT INTO a_game.competition (id, name, code, type, emblem)
        VALUES ($1, $2, $3, $4, $5) ON CONFLICT (id) DO 
        UPDATE SET
            name   = excluded.name,
            code   = excluded.code,
            type   = excluded.type,
            emblem = excluded.emblem
    """
    UPSERT_SEASON = """
        INSERT INTO a_game.season (id, competition_id, start_date, end_date)
        VALUES ($1, $2, $3, $4) ON CONFLICT (id) DO 
        UPDATE SET
            competition_id = excluded.competition_id,
            start_date     = excluded.start_date,
            end_date       = excluded.end_date
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            for comp in competitions:
                await conn.execute(
                    UPSERT_COMPETITION,
                    comp["id"],
                    comp["name"],
                    comp["code"],
                    comp["type"],
                    comp.get("emblem"),
                )
                log.info(f"Upserted competition: {comp['name']} (ID: {comp['id']})")

                # `currentSeason` means "most recent", not "live" — EC's is the 2024 Euros.
                # It is also the only way to learn about a season that has no matches yet
                # (PL 2502 starts 2026-08-21); historical seasons arrive via match payloads.
                season = comp.get("currentSeason") or {}
                if season.get("id") and season.get("startDate") and season.get("endDate"):
                    await conn.execute(
                        UPSERT_SEASON,
                        season["id"],
                        comp["id"],
                        date.fromisoformat(season["startDate"]),
                        date.fromisoformat(season["endDate"]),
                    )
                    log.info(f"Upserted season: {season['id']} for competition ID: {comp['id']}")
                else:
                    log.warning("Competition %s has no usable currentSeason; skipping season",
                                comp["code"])
    return len(competitions)
