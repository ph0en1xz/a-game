# ADR 0007 — Ingestion cadence: daily, with change-gated publish

- **Status:** Accepted
- **Date:** 2026-07-16
- **Amends:** ADR 0005 — its precompute-pipeline decision stands; the "every 6h" cadence in
  its Decision §1 and the "≤6h stale" consequence are replaced by the below.
- **Related:** ADR 0005 (precompute pipeline), ADR 0003 (rolling `[now, now+7d]` window),
  ADR 0002 (EKS-for-learning precedent)
- **Deciders:** Mario (Nexoro Tech)

## Context

ADR 0005 set ingestion at every 6h. That figure was never derived from data mutability — its
only stated justification is the negative consequence "≤6h stale — acceptable for weekly
fixtures", which argues the cadence is *tolerable*, not *necessary*.

The upstream data does not change on a 6-hour rhythm:

- Historical matches (`FINISHED`, past seasons) are immutable.
- Results land in clusters — weekend and midweek rounds.
- Scheduled fixtures are published weeks ahead; kickoff changes are rare.
- Standings change only as a function of results.

At 6h the pipeline runs ~28×/week, and roughly 4 of those runs meet changed data. Because
the "data ready" job is published unconditionally on every run, the calculation service
recomputes Elo + Poisson and re-narrates every upcoming fixture via Claude (Haiku) on every
cycle — ~10–20 Haiku calls per league per cycle (ADR 0005) — the large majority of them
against byte-identical inputs.

Two separable defects: the cadence is faster than the data, and the trigger is time-based
where it should be change-based.

## Decision

1. **Ingestion runs daily at 06:00 UTC** (`0 6 * * *`). Chosen to land after every weekend
   and midweek round completes, so results are ingested the morning after they occur.
2. **"data ready" is published only when a run changes Postgres state.** A run that changes
   nothing publishes nothing and exits 0.
3. **Change is detected at row level by the upsert.** No-op writes must not count as change:
   suppress them with `ON CONFLICT ... DO UPDATE ... WHERE <col> IS DISTINCT FROM
   excluded.<col>`, and count real changes via `RETURNING`.
4. **Historical backfill** — the 3-season training window (`input-spec.md` §10, decision 1)
   is a one-off bootstrap, not part of the recurring CronJob.

## Consequences

- **Positive:** pipeline runs drop from ~28/week to 7; Claude spend drops proportionally and
  now tracks real data change rather than wall-clock. Trigger semantics become honest —
  "data ready" fires when data is actually ready.
- **Negative:** worst-case staleness rises from ≤6h to ≤24h. Accepted: fixtures are weekly,
  and `api-spec.md` §4 already declares cadence a pipeline setting rather than an API
  contract. A midweek kickoff-time change could be up to a day stale.
- **Neutral:** the Kubernetes learning surface is unchanged — still one CronJob. If a later
  feature needs tighter freshness (live/in-play — ADR 0005 §6, Phase 2), that is a schedule
  change or a second CronJob split by cadence, not an architectural one.
- **Follow-up:** none outstanding — `docs/api-spec.md`, `docs/system-design/README.md`, and
  `TECHSTACK.md` are synced in this same change.
