CREATE TABLE a_game.commentary (
    id         BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- UNIQUE = one current preview per fixture, and the ON CONFLICT upsert target.
    -- No separate index needed: a UNIQUE constraint is implemented as a unique btree index.
    match_id   BIGINT      UNIQUE NOT NULL REFERENCES a_game.match(id),
    model_name TEXT        NOT NULL,
    prediction TEXT        NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
