# A-Game — Database Schema (v1)

> The Postgres schema the worker writes and the engine reads. Designed against
> `docs/input-spec.md` §2 (core inputs), ADR 0005 (Postgres = system of record), and ADR 0007
> (change-gated publish).

**Status:** Draft v1 · **Last updated:** 2026-07-16 · **Engine:** PostgreSQL 16

---

## 1. Scope

Four tables covering the **ingestion** half of the pipeline: the match facts the worker
upserts and the engine trains on. Predictions are **not** modelled here yet — ADR 0005 §2
makes them a permanent, model-versioned record for calibration, and they arrive with the
engine. They will FK to `match.id`.

No ORM. `asyncpg` + hand-written SQL — the workload is one upsert with change detection and
a set of analytical reads (window functions, time-decayed aggregates), which is where ORMs
cost more than they save. pydantic covers boundary typing (`TECHSTACK.md:19`).

## 2. Tables

### `team`

Seeded from the `homeTeam`/`awayTeam` objects embedded in every match payload — **no separate
API call**. Current-state: a rebrand rewrites the display name on historical fixtures, which
is acceptable for this product.

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | `bigint` | no | **PK.** football-data's team id (e.g. `402`) |
| `team_name` | `text` | no | `"Brentford FC"` |
| `team_shortname` | `text` | yes | `"Brentford"` |
| `team_tla` | `text` | yes | `"BRE"` |
| `team_emblem` | `text` | yes | crest URL |

### `competition`

Seeded by `GET /v4/competitions` — step 1 of every daily run, one call, returning all 13
competitions on the free tier with their `currentSeason` block (which seeds `season` too).
Idempotent, so there is no separate bootstrap step; a wiped database self-heals on the next
run. The `competition` object embedded in each match payload keeps the row fresh as a
by-product. Backs `GET /v1/leagues` (`api-spec.md` §6).

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | `bigint` | no | **PK.** football-data's competition id (e.g. `2021`) |
| `name` | `text` | no | `"Premier League"` |
| `code` | `text` | no | **UNIQUE.** `"PL"` — the `{league}` path param (ADR 0003, superseded clause: codes only, no slugs) |
| `type` | `text` | no | `LEAGUE` \| `CUP`. Cups bring group stages and `PENALTIES` — see `input-spec.md` §10 decision 5 |
| `emblem` | `text` | yes | emblem URL |
| `enabled` | `boolean` | no | Default `false`. Which competitions the daily worker fetches — `SELECT code FROM competition WHERE enabled`. Widening is an `UPDATE`, not a deploy. **The competition upsert must never touch this column** (only `INSERT` sets it), or every run resets your selection |

### `season`

Seeded from two places: `currentSeason` in the `GET /v4/competitions` response (the live
season), and the `season` object embedded in every match payload (whichever season those
matches belong to — this is how the 3-season training window gets its dates).

One row per competition-season. `start_date`/`end_date` are why this is a table and not a
column on `match`: they're facts about the season, and Elo needs them to detect the season
boundary where ratings regress toward the mean (`input-spec.md` §10 decisions 4/6).

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | `bigint` | no | **PK.** football-data's season id (e.g. `2403`) — *not* the year |
| `competition_id` | `bigint` | no | **FK → `competition.id`.** One competition, many seasons |
| `start_date` | `date` | no | `2025-08-15` |
| `end_date` | `date` | no | `2026-05-24` |

### `match`

The foundation table. Holds both `SCHEDULED` and `FINISHED` states — same entity, same row,
updated in place by the daily upsert. Competition is reachable via `season`, so there is
deliberately **no** `competition_id` here (it would be a transitive dependency free to drift).

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | `bigint` | no | **PK.** football-data's match id (e.g. `538107`). The `ON CONFLICT` target |
| `season_id` | `bigint` | no | **FK → `season.id`** |
| `home_team_id` | `bigint` | no | **FK → `team.id`.** True from the moment a fixture is scheduled |
| `away_team_id` | `bigint` | no | **FK → `team.id`** |
| `matchday` | `smallint` | yes | This match's matchday (`33`) — **not** `season.currentMatchday`. Null for some cup stages. Serves `?matchday=` (`api-spec.md` §3) |
| `utc_date` | `timestamptz` | no | Kickoff. Feeds recency decay and rest-day gaps (`input-spec.md` §3) |
| `status` | `text` | no | `SCHEDULED` \| `FINISHED` \| … CHECK-constrained |
| `duration` | `text` | yes | `REGULAR` \| `EXTRA_TIME` \| `PENALTIES` |
| `home_goals` | `smallint` | yes | Null until played. Poisson attack/defence input |
| `away_goals` | `smallint` | yes | Null until played |
| `home_goals_ht` | `smallint` | yes | Half-time |
| `away_goals_ht` | `smallint` | yes | Half-time |
| `fulltime_outcome` | `text` | yes | `HOME_WIN` \| `DRAW` \| `AWAY_WIN`. Elo input. **Derive from the goals** — `GENERATED ALWAYS AS`, never hand-maintained beside its own source |
| `referee` | `text` | yes | Nothing in v1 reads this; kept for later |
| `blob` | `jsonb` | no | The raw match object. Insurance against the 10-req/min ceiling — backfill a new column from your own rows instead of re-fetching three seasons |
| `ingested_at` | `timestamptz` | no | When this row was last written |

## 3. Change detection (ADR 0007 §3)

The worker publishes "data ready" **only** when an upsert changes state. The columns that
count as change:

```
status, utc_date, home_goals, away_goals, home_goals_ht, away_goals_ht
```

Suppress no-op writes so they don't fire the trigger:

```sql
ON CONFLICT (id) DO UPDATE SET ...
WHERE match.status        IS DISTINCT FROM excluded.status
   OR match.utc_date      IS DISTINCT FROM excluded.utc_date
   OR match.home_goals    IS DISTINCT FROM excluded.home_goals
   OR match.away_goals    IS DISTINCT FROM excluded.away_goals
   OR match.home_goals_ht IS DISTINCT FROM excluded.home_goals_ht
   OR match.away_goals_ht IS DISTINCT FROM excluded.away_goals_ht
RETURNING id;
```

**Never `lastUpdated`.** Upstream bulk-touches it: all 13 matches from the 2026-04-18→22
round carry an identical `lastUpdated` of `2026-06-07T20:20:25Z`, six weeks after they were
played, with no score change. Keying on it fires 13 false changes and a full Haiku rerun for
nothing.

## 4. Run order

Two phases per daily run. FKs dictate the order within each.

**Phase 1 — `GET /v4/competitions`** (1 call). Upsert `competition`, then `season` from each
`currentSeason`. Never writes `enabled`.

**Phase 2 — `GET /v4/competitions/{code}/matches`**, once per enabled competition
(`SELECT code FROM competition WHERE enabled`). Each payload upserts `competition` → `season`
→ `team` → `match`, in that order, one transaction per competition. Pace the calls ~6s apart
— the free tier caps at ~10 req/min, so a league loop is sequential, never `asyncio.gather`.

Publish "data ready" once at the end, only if any phase-2 upsert changed rows (§3).

## 5. Deliberately absent

| Not modelled | Why |
|---|---|
| `predictions` | Product-critical (ADR 0005 §2) but nothing writes it yet. FKs to `match.id` when the engine lands |
| `standings` | Reconstructable from `match` rows. Earns a table only if HFA tuning wants an independent check |
| `scorers`, players/squads | Player data feeds nothing in v1 — Elo and Poisson are team-level. `input-spec.md` §9's v2 injury weighting is the first real consumer |
| `lastUpdated` | See §3 |
| `area`, `odds`, crest/flag URLs beyond the two emblems | Redundant, or dead on the free tier. In `blob` if ever needed |
| `stage`, `group` | Not needed for league-only play; CL is deferred (`input-spec.md` §10 decision 5). In `blob` |
| League slugs / aliases | Dropped — see ADR 0003's superseded clause |

## 6. Open

1. **Migration tooling.** Numbered `.sql` files to start; Alembic (raw-SQL migrations, no ORM
   models) once a change lands against a populated table.
2. **Enum vs CHECK vs lookup** for `status`, `duration`, `fulltime_outcome`. CHECK to start —
   `ALTER TYPE` is a migration and these sets are small and closed.
3. **Indexes.** None yet, deliberately. The daily worker is a PK upsert; the engine reads
   `(season_id, utc_date)` windows. Add when a query proves the need, not before.

## Related

- `docs/input-spec.md` §2 — the core inputs this table exists to hold
- `docs/api-spec.md` — the contract these rows are read through
- ADR 0005 — Postgres as system of record
- ADR 0007 — daily cadence, change-gated publish
