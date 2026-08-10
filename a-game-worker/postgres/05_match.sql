CREATE TABLE IF NOT EXISTS a_game.match (
    id               BIGINT      PRIMARY KEY,
    season_id        BIGINT      NOT NULL REFERENCES a_game.season(id),
    home_team_id     BIGINT      NOT NULL REFERENCES a_game.team(id),
    away_team_id     BIGINT      NOT NULL REFERENCES a_game.team(id),
    matchday         SMALLINT,
    utc_date         TIMESTAMPTZ NOT NULL,
    status           TEXT        NOT NULL,
    duration         TEXT,
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
    blob             JSONB       NOT NULL,
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
