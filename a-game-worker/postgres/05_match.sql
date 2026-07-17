-- The foundation table (input-spec.md §2). Holds both SCHEDULED and FINISHED states — same
-- entity, same row, updated in place by the daily upsert.
--
-- No competition_id here on purpose: season determines competition, so storing both would be
-- a transitive dependency free to drift.
CREATE TABLE a_game.match (
    -- football-data's own match id (e.g. 538107). Supplied on insert, NOT generated: it is
    -- the ON CONFLICT target that makes the daily upsert an upsert (ADR 0007 §3).
    id               BIGINT      PRIMARY KEY,
    season_id        BIGINT      NOT NULL REFERENCES a_game.season(id),
    -- True from the moment a fixture is scheduled — never null, even before kickoff.
    home_team_id     BIGINT      NOT NULL REFERENCES a_game.team(id),
    away_team_id     BIGINT      NOT NULL REFERENCES a_game.team(id),

    -- This match's matchday (33), NOT season.currentMatchday (38). Null for some cup stages.
    matchday         SMALLINT,
    -- Kickoff instant. timestamptz, not timestamp: the payload is '2026-04-18T11:30:00Z'.
    utc_date         TIMESTAMPTZ NOT NULL,
    status           TEXT        NOT NULL,
    duration         TEXT,

    -- Null until played. A SCHEDULED fixture has no score — NOT NULL here would make the
    -- daily worker (which fetches status=SCHEDULED) unable to insert anything at all.
    home_goals       SMALLINT    CHECK (home_goals    >= 0),
    away_goals       SMALLINT    CHECK (away_goals    >= 0),
    home_goals_ht    SMALLINT    CHECK (home_goals_ht >= 0),
    away_goals_ht    SMALLINT    CHECK (away_goals_ht >= 0),

    -- Elo's input, derived from the goals rather than hand-maintained beside its own source.
    fulltime_outcome TEXT GENERATED ALWAYS AS (
        CASE
            WHEN home_goals IS NULL OR away_goals IS NULL THEN NULL
            WHEN home_goals > away_goals THEN 'HOME_WIN'
            WHEN home_goals < away_goals THEN 'AWAY_WIN'
            ELSE 'DRAW'
        END
    ) STORED,

    referee_name     TEXT,                    -- nothing in v1 reads this
    -- The raw match object. Insurance against the ~10 req/min ceiling: backfill a new column
    -- from your own rows instead of re-fetching three seasons.
    blob             JSONB       NOT NULL,
    -- Last written, not first created — the upsert must SET this explicitly on conflict.
    ingested_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT match_teams_differ CHECK (home_team_id <> away_team_id),
    CONSTRAINT match_status_valid CHECK (
        status IN ('SCHEDULED', 'TIMED', 'IN_PLAY', 'PAUSED',
                   'FINISHED', 'SUSPENDED', 'POSTPONED', 'CANCELLED', 'AWARDED')
    ),
    CONSTRAINT match_duration_valid CHECK (
        duration IS NULL
        OR duration IN ('REGULAR', 'EXTRA_TIME', 'PENALTY_SHOOTOUT')
    )
);
