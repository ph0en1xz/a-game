import logging
from datetime import date

import asyncpg

from app.model import Match

log = logging.getLogger("worker.db")

async def fetch_rabbit_events(pool: asyncpg.Pool) -> list[asyncpg.Record]:
    """Pending outbox events, oldest first. Empty list when there is nothing to publish."""
    QUERY = """
        SELECT id, match_id
        FROM a_game.rabbit_event
        ORDER BY id
    """

    async with pool.acquire() as conn:
        rows = await conn.fetch(QUERY)

    log.info("Fetched %d pending rabbit events", len(rows))
    return rows


async def delete_rabbit_event(pool: asyncpg.Pool, ids: list[int]) -> None:
    """Drop events already published. Called only after a successful publish."""
    QUERY = """
        DELETE FROM a_game.rabbit_event
        WHERE id = ANY($1::bigint[])
    """

    async with pool.acquire() as conn:
        await conn.execute(QUERY, ids)

    log.info("Deleted %d published rabbit events", len(ids))


async def sync_matches_per_competition(pool: asyncpg.Pool, matches: list[Match]) -> int:
    # Written inside the match transaction below — that atomicity is the whole point.
    RABBIT_EVENT_QUERY = """
        INSERT INTO a_game.rabbit_event (event_type, match_id)
        VALUES ($1, $2)
    """
    
    # Only guarantee the season row exists for the match's FK — never overwrite it.
    # sync_competitions owns season data; this backfills the gap (skipped/historical seasons).
    SEASON_QUERY = """
        INSERT INTO a_game.season (id, competition_id, start_date, end_date)
        VALUES ($1, $2, $3, $4) ON CONFLICT (id) DO NOTHING
    """

    TEAMS_QUERY = """
        INSERT INTO a_game.team (id, name, shortname, tla, emblem)
        VALUES ($1, $2, $3, $4, $5) ON CONFLICT (id) DO
        UPDATE SET
            name      = excluded.name,
            shortname = excluded.shortname,
            tla       = excluded.tla,
            emblem    = excluded.emblem
    """

    # fulltime_outcome is GENERATED and ingested_at defaults — neither is inserted. The WHERE on
    # the UPDATE is the change gate (ADR 0007 §3): an unchanged row updates nothing and RETURNING
    # yields no row, so a no-op run counts zero. NEVER key the gate on lastUpdated.
    MATCH_QUERY = """
        INSERT INTO a_game.match (
            id, season_id, home_team_id, away_team_id,
            matchday, utc_date, status, duration,
            home_goals, away_goals, home_goals_ht, away_goals_ht,
            referee_name, blob
        )
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14::jsonb)
        ON CONFLICT (id) DO
        UPDATE SET
            season_id     = excluded.season_id,
            home_team_id  = excluded.home_team_id,
            away_team_id  = excluded.away_team_id,
            matchday      = excluded.matchday,
            utc_date      = excluded.utc_date,
            status        = excluded.status,
            duration      = excluded.duration,
            home_goals    = excluded.home_goals,
            away_goals    = excluded.away_goals,
            home_goals_ht = excluded.home_goals_ht,
            away_goals_ht = excluded.away_goals_ht,
            referee_name  = excluded.referee_name,
            blob          = excluded.blob,
            ingested_at   = NOW()
        WHERE match.status        IS DISTINCT FROM excluded.status
           OR match.utc_date      IS DISTINCT FROM excluded.utc_date
           OR match.home_goals    IS DISTINCT FROM excluded.home_goals
           OR match.away_goals    IS DISTINCT FROM excluded.away_goals
           OR match.home_goals_ht IS DISTINCT FROM excluded.home_goals_ht
           OR match.away_goals_ht IS DISTINCT FROM excluded.away_goals_ht
        RETURNING id
    """
    
    changed = 0
    for match in matches:
        teams = [match.homeTeam, match.awayTeam]
        season = match.season
        score_info = match.score
        referees = match.referees

        async with pool.acquire() as conn, conn.transaction():
            await conn.execute(
                SEASON_QUERY,
                season.id,
                match.competition.id,
                season.startDate,
                season.endDate,
            )
            for team in teams:
                # payload calls the crest URL `crest`; the column is `emblem`.
                await conn.execute(
                    TEAMS_QUERY,
                    team.id,
                    team.name,
                    team.shortName,
                    team.tla,
                    team.crest,
                )
            row = await conn.fetchrow(
                MATCH_QUERY,
                match.id,
                season.id,
                match.homeTeam.id,
                match.awayTeam.id,
                match.matchday,
                match.utcDate,
                match.status,
                score_info.duration,
                score_info.fullTime.home,
                score_info.fullTime.away,
                score_info.halfTime.home,
                score_info.halfTime.away,
                referees[0].name if referees else None,
                match.model_dump_json(),
            )
            # No row means the change gate found nothing new — no event owed.
            if row is not None:
                changed += 1
                await conn.execute(
                    RABBIT_EVENT_QUERY,
                    "match.changed",
                    row["id"],
                )

    log.info("Synced %d changed matches of %d fetched", changed, len(matches))
    return changed


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
    async with pool.acquire() as conn, conn.transaction():
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
