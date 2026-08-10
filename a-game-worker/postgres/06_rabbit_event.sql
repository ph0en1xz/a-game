CREATE TABLE IF NOT EXISTS a_game.rabbit_event (
    id         BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_type TEXT        NOT NULL,
    match_id   BIGINT      NOT NULL REFERENCES a_game.match(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
