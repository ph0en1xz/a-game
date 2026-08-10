CREATE TABLE IF NOT EXISTS a_game.commentary (
    id           BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- UNIQUE = one current preview per fixture, and the ON CONFLICT upsert target.
    -- No separate index needed: a UNIQUE constraint is implemented as a unique btree index.
    match_id     BIGINT      UNIQUE NOT NULL REFERENCES a_game.match(id),
    source_model TEXT        NOT NULL,
    prediction   TEXT        NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE IF NOT EXISTS a_game.commentary
ADD COLUMN IF NOT EXISTS suggested_bet        TEXT NOT NULL DEFAULT '',
ADD COLUMN IF NOT EXISTS suggested_bet_reason TEXT NOT NULL DEFAULT '';