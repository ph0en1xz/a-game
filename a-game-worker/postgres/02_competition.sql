-- Seeded by GET /v4/competitions (phase 1 of every run) and refreshed as a by-product of
-- the `competition` object embedded in each match payload.
CREATE TABLE a_game.competition (
    -- football-data's own competition id (e.g. 2021 = Premier League). Supplied by us on
    -- insert, NOT generated: it is the ON CONFLICT target for the daily upsert.
    id      BIGINT  PRIMARY KEY,
    name    TEXT    NOT NULL,
    code    TEXT    NOT NULL UNIQUE,          -- 'PL' — the {league} path param
    type    TEXT    NOT NULL
        CONSTRAINT competition_type_valid CHECK (type IN ('LEAGUE', 'CUP')),
    emblem  TEXT,                             -- a URL, not binary
    -- Which competitions the daily worker fetches. The phase-1 upsert must NEVER write this
    -- column (only INSERT sets it), or every run resets the selection.
    enabled BOOLEAN NOT NULL DEFAULT FALSE
);
