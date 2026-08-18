-- Development seed data — NOT part of the schema init.
--
-- Deliberately kept out of a-game-worker/postgres/, which is the init-script
-- directory: fake matches must never auto-load into a fresh database.
--
-- Ids are real football-data.org ids (competition 2021 = Premier League,
-- team 57 = Arsenal, and so on), so a later real ingestion collides on the
-- primary key and leaves these rows alone rather than duplicating them.
-- Match ids are in a 5xxxxx block that football-data does not use.
--
-- Idempotent: safe to run repeatedly, and safe against an already-populated
-- database.
--
-- Kickoffs are relative to now() rather than literal dates, and the match upsert
-- refreshes them, so re-running always leaves three played fixtures behind and
-- three still to come. Hardcoded dates went stale within a fortnight and the
-- previous DO NOTHING meant re-seeding could never repair them.
--
-- Run against the in-cluster Postgres:
--   kubectl exec -i -n a-game a-game-postgres-0 -- psql -U <user> -d <db> < scripts/seed_dev.sql
-- User and database come from the db-credentials Secret — the same values brain uses.

BEGIN;

INSERT INTO a_game.competition (id, name, code, type, emblem, enabled) VALUES
  (2021, 'Premier League', 'PL', 'LEAGUE', 'https://crests.football-data.org/PL.png', TRUE)
ON CONFLICT (id) DO NOTHING;

INSERT INTO a_game.team (id, name, shortname, tla, emblem) VALUES
  (57, 'Arsenal FC',            'Arsenal',    'ARS', 'https://crests.football-data.org/57.png'),
  (61, 'Chelsea FC',            'Chelsea',    'CHE', 'https://crests.football-data.org/61.png'),
  (64, 'Liverpool FC',          'Liverpool',  'LIV', 'https://crests.football-data.org/64.png'),
  (65, 'Manchester City FC',    'Man City',   'MCI', 'https://crests.football-data.org/65.png'),
  (66, 'Manchester United FC',  'Man United', 'MUN', 'https://crests.football-data.org/66.png'),
  (67, 'Newcastle United FC',   'Newcastle',  'NEW', 'https://crests.football-data.org/67.png'),
  (62, 'Everton FC',            'Everton',    'EVE', 'https://crests.football-data.org/62.png'),
  (73, 'Tottenham Hotspur FC',  'Tottenham',  'TOT', 'https://crests.football-data.org/73.png')
ON CONFLICT (id) DO NOTHING;

INSERT INTO a_game.season (id, competition_id, start_date, end_date, winner_id) VALUES
  (2400, 2021, '2026-08-01', '2027-05-30', NULL)
ON CONFLICT (id) DO NOTHING;

-- fulltime_outcome is a GENERATED column — never list it here.
INSERT INTO a_game.match
  (id, season_id, home_team_id, away_team_id, matchday, utc_date, status,
   duration, home_goals, away_goals, home_goals_ht, away_goals_ht, blob) VALUES
  -- Played: goals present, so fulltime_outcome derives to HOME_WIN / AWAY_WIN / DRAW.
  (500001, 2400, 57, 61, 1, ((current_date - 7) + time '14:00') AT TIME ZONE 'UTC', 'FINISHED', 'REGULAR', 3, 1, 1, 0, '{}'::jsonb),
  (500002, 2400, 62, 64, 1, ((current_date - 7) + time '16:30') AT TIME ZONE 'UTC', 'FINISHED', 'REGULAR', 0, 2, 0, 1, '{}'::jsonb),
  (500003, 2400, 66, 73, 1, ((current_date - 6) + time '15:00') AT TIME ZONE 'UTC', 'FINISHED', 'REGULAR', 2, 2, 1, 1, '{}'::jsonb),
  -- Upcoming: goals NULL, so fulltime_outcome stays NULL. These are the preview cases —
  -- two real team names, a real kickoff, and no result for the model to leak.
  (500004, 2400, 65, 57, 2, ((current_date + 3) + time '14:00') AT TIME ZONE 'UTC', 'SCHEDULED', NULL, NULL, NULL, NULL, NULL, '{}'::jsonb),
  (500005, 2400, 64, 66, 2, ((current_date + 3) + time '16:30') AT TIME ZONE 'UTC', 'TIMED',     NULL, NULL, NULL, NULL, NULL, '{}'::jsonb),
  (500006, 2400, 73, 67, 2, ((current_date + 4) + time '15:00') AT TIME ZONE 'UTC', 'SCHEDULED', NULL, NULL, NULL, NULL, NULL, '{}'::jsonb)
-- Only the two columns that go stale. Goals stay untouched: these ids sit in a
-- 5xxxxx block football-data never issues, so nothing real ever overwrites them.
ON CONFLICT (id) DO UPDATE SET
  utc_date = EXCLUDED.utc_date,
  status   = EXCLUDED.status;

COMMIT;

-- Verify:
--   SELECT m.id, ht.name AS home_team, at.name AS away_team, m.utc_date, m.status, m.fulltime_outcome
--   FROM a_game.match m
--   LEFT JOIN a_game.team ht ON ht.id = m.home_team_id
--   LEFT JOIN a_game.team at ON at.id = m.away_team_id
--   WHERE m.id BETWEEN 500001 AND 500006
--   ORDER BY m.id;
