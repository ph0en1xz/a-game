CREATE TABLE IF NOT EXISTS a_game.team (
    id        BIGINT PRIMARY KEY,
    name      TEXT   NOT NULL,
    shortname TEXT,
    tla       TEXT,
    emblem    TEXT              -- a crest URL, not binary
);
