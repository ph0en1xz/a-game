# A-Game — Database Schema (v1)

> The Postgres schema the worker writes and the engine reads. Designed against
> `docs/input-spec.md` §2 (core inputs), ADR 0005 (Postgres = system of record), and ADR 0007
> (change-gated publish).

**Status:** Draft v3 · **Last updated:** 2026-08-11 · **Engine:** PostgreSQL 16

---

## 1. Scope

Six tables. Four cover the **ingestion** half — the match facts the worker upserts and the
engine trains on. Two cover the **output** half: `prediction` (the engine's numbers) and
`commentary` (the LLM's prose). Both FK to `match.id`.

Nothing here models API keys. The single key's hash lives in a Kubernetes Secret
(`api-spec.md` §2, decided 2026-08-11) — a table only earns its place with several keys to
tell apart or revocation without a redeploy.

The output half is split in two deliberately. The engine and the LLM fail independently —
LiteLLM can be down while the maths is fine — so one row holding both would have to either
block on the model or store a lie. Their key shapes differ too, for the reason in §2.5.

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
by-product. Its `code` column no longer appears in any request — the API is keyed by match id
as of api-spec v4 (2026-08-11), so neither the `{league}` path param nor the `GET /v1/leagues`
discovery endpoint exists. `code` still drives which competitions the worker fetches.

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

### `prediction`

The engine's output for one fixture under one parameter set. ADR 0005 §2 makes these
permanent and model-versioned; ADR 0010 defines what the version means and §15–18 of it
define how two versions get compared. Written by the brain. **Not read by the API** — the
2026-08-10 contract change (`api-spec.md` v3) keeps the raw numbers out of the client
payload; this table serves calibration and the backtest.

**Applied 2026-08-10** — DDL in `a-game-worker/postgres/08_prediction.sql`, applied by hand
to the running database (init scripts don't re-run against an existing PVC).

Probabilities are **typed columns, not JSONB**. They are exactly what calibration buckets
over, and JSONB would give them no type check, no NOT NULL, no planner statistics, and an
expression index on every calibration query. `numeric`, never `float` — a binary float
cannot hold 0.1, so a sum-to-one constraint would fail on rows that are actually correct.

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | `bigint` | no | **PK.** `GENERATED ALWAYS AS IDENTITY` |
| `match_id` | `bigint` | no | **FK → `match.id`** |
| `engine_version` | `text` | no | The ADR 0010 parameter set that produced this row (`app/engine/params.py`). Bumps on any parameter change, not just a formula change |
| `prob_home` | `numeric(5,4)` | no | From the Poisson score matrix (ADR 0010 §12), not from Elo |
| `prob_draw` | `numeric(5,4)` | no | Falls out of the matrix — no three-outcome Elo variant needed |
| `prob_away` | `numeric(5,4)` | no | |
| `over_2_5` | `numeric(5,4)` | no | |
| `btts` | `numeric(5,4)` | no | |
| `lambda_home` | `numeric(6,4)` | no | Poisson λ for the home side. The two λs regenerate the whole score matrix, so a new market (correct score, over 1.5, …) is derivable later without replaying the engine |
| `lambda_away` | `numeric(6,4)` | no | |
| `most_likely_scores` | `jsonb` | no | `[{"home": 1, "away": 1, "prob": 0.1123}, …]` — a list, nested, never filtered |
| `elo_home` | `numeric(7,2)` | no | Rating at kickoff, straight from `RatingSnapshot` — context and cross-check (ADR 0010 §14), never a probability source |
| `elo_away` | `numeric(7,2)` | no | |
| `computed_at` | `timestamptz` | no | `DEFAULT NOW()`, refreshed on upsert |

Constraints:

- `UNIQUE (match_id, engine_version)` — the business key, and the `ON CONFLICT` target.
- `CHECK` each probability is in `[0,1]`, and `ABS(prob_home + prob_draw + prob_away - 1) <=
  0.0005`. Tolerance rather than equality, because rounding to four places need not land on
  exactly 1 — the writer rounds to column scale before insert so the CHECK applies to the
  stored values.
- No separate index on `match_id`: the UNIQUE builds a btree on `(match_id, engine_version)`
  and a composite index already serves a leading-column lookup.

**`hfa` is not stored.** The API payload reports it (`api-spec.md` §5), but it is a constant
of `engine_version` — a transitive dependency free to drift if copied onto every row. Resolve
it from the version's config at read time.

### `commentary`

The LLM's preview for one fixture. Applied 2026-08-07 (originally as `a_game.prediction`;
renamed the same day, before any rows or readers existed — a table called `prediction`
holding one prose column would have misled every reader once the table above landed). DDL:
`a-game-worker/postgres/07_commentary.sql`.

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | `bigint` | no | **PK.** `GENERATED ALWAYS AS IDENTITY` |
| `match_id` | `bigint` | no | **FK → `match.id`.** `UNIQUE` — one current preview per fixture |
| `source_model` | `text` | no | The **resolved provider model** (`anthropic/claude-haiku-4-5-…`), read from the gateway's `x-litellm-model-name` response header — not the alias, so a fallback to the secondary provider is recorded honestly. (Renamed from `model_name` and re-pointed 2026-08-10 — the alias told you nothing when the fallback fired) |
| `prediction` | `text` | no | The preview. 40–600 chars, enforced by the `Commentary` pydantic model |
| `suggested_bet` | `text` | no | `DEFAULT ''`. The market where the model departs most from the league baseline — **in either direction**: a divergence signal, not a tip (decided 2026-08-10). Validated against the engine's market list in code, deliberately not by a CHECK — the list will grow, and a CHECK turns adding a market into a migration |
| `suggested_bet_reason` | `text` | no | `DEFAULT ''`. One sentence citing the model percentage and the baseline, ≤200 chars enforced in code |
| `created_at` | `timestamptz` | no | `DEFAULT NOW()` |
| `updated_at` | `timestamptz` | no | `DEFAULT NOW()`, refreshed by the upsert's `DO UPDATE` |

The two `suggested_bet` columns were added 2026-08-10 as an idempotent `ALTER` in
`07_commentary.sql` and applied by hand to the running database.

Prompt and cost are deliberately absent — they belong to Langfuse (ADR 0008 T1), and storing
the prompt would duplicate a constant system prompt onto every row.

### 2.5 Why the two output tables key differently

`commentary` is unique on `match_id` alone: one current preview per fixture, overwritten on
recompute. `prediction` is unique on `(match_id, engine_version)`: calibration needs the same
fixture scored by v3 and v4 side by side, which is the entire point of ADR 0005 §2.

Both upsert within their key rather than appending. That means the *trajectory* of a
prediction — how the numbers moved as kickoff approached — is not kept. Acceptable while
`betting` is null; revisit if value bets ever need the prediction as of the moment a price
was taken.

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
| `team_rating` | Elo state per team+competition. ADR 0010 §21 leaves it open — ~1,140 matches recompute instantly, so the question is only whether rating *history* is wanted |
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
3. **Indexes.** None on the ingestion tables, deliberately — the daily worker is a PK upsert
   and the engine reads `(season_id, utc_date)` windows. Add when a query proves the need.
   The output tables' only indexes are the ones their UNIQUE constraints build.
4. ~~The `prediction` table DDL~~ — **done 2026-08-10**: written as
   `08_prediction.sql` and applied to the running database. (The `prediction` →
   `commentary` rename was done 2026-08-07.)
5. **Backtest storage.** ADR 0010 §15–18 needs Brier and log loss per `engine_version` over a
   held-out season. Computable from `prediction` joined to `match` — decide whether the
   scores get their own table or stay a query.

## Related

- `docs/input-spec.md` §2 — the core inputs this table exists to hold
- `docs/api-spec.md` — the contract these rows are read through
- ADR 0005 — Postgres as system of record
- ADR 0007 — daily cadence, change-gated publish
- ADR 0010 — engine parameters, `engine_version` semantics, and the evaluation gate
