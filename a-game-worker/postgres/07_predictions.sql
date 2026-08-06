CREATE TABLE a_game.prediction (
    id         BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    match_id   BIGINT      NOT NULL REFERENCES a_game.match(id),
    model_name TEXT        NOT NULL,
    prompt     TEXT        NOT NULL,
    cost       SMALLINT    NOT NULL,
    prediction JSONB       NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);