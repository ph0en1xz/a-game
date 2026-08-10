CREATE TABLE IF NOT EXISTS a_game.competition (
    id      BIGINT  PRIMARY KEY,
    name    TEXT    NOT NULL,
    code    TEXT    NOT NULL UNIQUE,          -- 'PL' — the {league} path param
    type    TEXT    NOT NULL
        CONSTRAINT competition_type_valid CHECK (type IN ('LEAGUE', 'CUP')),
    emblem  TEXT,                             -- a URL, not binary
    enabled BOOLEAN NOT NULL DEFAULT FALSE
);
