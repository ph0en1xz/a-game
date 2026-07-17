-- Seeded from the homeTeam/awayTeam objects embedded in every match payload — no separate
-- API call. Current-state: a rebrand rewrites the name on historical fixtures (accepted).
CREATE TABLE a_game.team (
    -- football-data's own team id (e.g. 402 = Brentford). Supplied on insert, NOT generated.
    id        BIGINT PRIMARY KEY,
    name      TEXT   NOT NULL,
    shortname TEXT,
    tla       TEXT,
    emblem    TEXT              -- a crest URL, not binary
);
