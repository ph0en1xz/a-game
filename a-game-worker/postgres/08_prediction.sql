CREATE TABLE IF NOT EXISTS a_game.prediction (
    id                 BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    match_id           BIGINT      NOT NULL REFERENCES a_game.match(id),

    -- Every parameter in app/engine/params.py is part of this string (ADR 0010 §15).
    -- Kept per row because ADR 0005 §2 scores calibration WITHIN a version and
    -- ADR 0010 §16 requires beating the previous one - both need the old rows alive.
    engine_version     TEXT        NOT NULL,

    -- 1X2 from the Poisson score matrix (ADR 0010 §12). Elo is not the source.
    prob_home          NUMERIC(5,4) NOT NULL CHECK (prob_home BETWEEN 0 AND 1),
    prob_draw          NUMERIC(5,4) NOT NULL CHECK (prob_draw BETWEEN 0 AND 1),
    prob_away          NUMERIC(5,4) NOT NULL CHECK (prob_away BETWEEN 0 AND 1),

    over_2_5           NUMERIC(5,4) NOT NULL CHECK (over_2_5 BETWEEN 0 AND 1),
    btts               NUMERIC(5,4) NOT NULL CHECK (btts     BETWEEN 0 AND 1),

    -- The Poisson goal rates the matrix was built from. Stored so a new market
    -- (correct score, over 1.5, ...) can be derived later without replaying the
    -- engine - these two numbers regenerate the whole matrix exactly.
    lambda_home        NUMERIC(6,4) NOT NULL CHECK (lambda_home >= 0),
    lambda_away        NUMERIC(6,4) NOT NULL CHECK (lambda_away >= 0),

    -- Top scorelines with their probabilities, e.g.
    -- [{"home": 1, "away": 1, "prob": 0.1123}, ...]. A list, so it does not fit
    -- columns without inventing score_1_home, score_2_home and so on.
    most_likely_scores JSONB        NOT NULL,

    -- Ratings as they stood at kickoff, straight from RatingSnapshot. Payload
    -- context and a cross-check on the Poisson output (ADR 0010 §14), never a
    -- probability source.
    elo_home           NUMERIC(7,2) NOT NULL,
    elo_away           NUMERIC(7,2) NOT NULL,

    computed_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    -- One prediction per fixture per engine version. Recomputing under the same
    -- version overwrites (ON CONFLICT target); a version bump inserts alongside
    -- rather than replacing, which is what keeps §16's comparison possible.
    CONSTRAINT prediction_match_version_key UNIQUE (match_id, engine_version),

    -- Rounding to 4dp can leave the three a hair off 1.0; anything beyond that
    -- is a bug in the matrix, not float noise.
    CONSTRAINT prediction_1x2_sums_to_one CHECK (
        ABS(prob_home + prob_draw + prob_away - 1) <= 0.0005
    )
);

-- Serving reads "the current prediction for this fixture", which without an index
-- means a sequential scan once the backfill has thousands of rows. The UNIQUE
-- constraint above indexes (match_id, engine_version) and Postgres can use its
-- leading column, so this is only worth adding if that proves insufficient:
-- CREATE INDEX prediction_match_id_idx ON a_game.prediction (match_id);
