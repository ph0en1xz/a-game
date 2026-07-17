-- Seeded from `currentSeason` in GET /v4/competitions (the live season) and from the
-- `season` object embedded in each match payload (whichever season those matches belong to
-- — this is how the 3-season training window gets its dates).
--
-- start_date/end_date are why this is a table and not a column on `match`: Elo needs them to
-- detect the season boundary where ratings regress toward the mean (input-spec.md §10).
CREATE TABLE a_game.season (
    -- football-data's own season id (e.g. 2403). NOT the year, and NOT generated.
    id             BIGINT PRIMARY KEY,
    competition_id BIGINT NOT NULL REFERENCES a_game.competition(id),
    start_date     DATE   NOT NULL,
    end_date       DATE   NOT NULL,
    -- Only set once a season completes; null for the current one.
    -- NOTE: for EC/WC the winner is a national team, which only exists in `team` if you
    -- ingested its matches. FK will fail on those competitions until you do.
    winner_id      BIGINT REFERENCES a_game.team(id),

    CONSTRAINT season_dates_ordered CHECK (end_date > start_date)
);
