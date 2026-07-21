CREATE TABLE a_game.season (
    id             BIGINT PRIMARY KEY,
    competition_id BIGINT NOT NULL REFERENCES a_game.competition(id),
    start_date     DATE   NOT NULL,
    end_date       DATE   NOT NULL,
    winner_id      BIGINT REFERENCES a_game.team(id),
        CONSTRAINT season_dates_ordered CHECK (end_date > start_date)
);
