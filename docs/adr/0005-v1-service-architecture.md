# ADR 0005 — v1 service architecture: precompute pipeline, single broker, Postgres as prediction record

- **Status:** Accepted
- **Date:** 2026-07-09
- **Amends:** ADR 0003 — its API-only-SaaS decision stands; its endpoint set is replaced by
  the one below (see `docs/api-spec.md` v2).
- **Related:** ADR 0002 (EKS-for-learning precedent), ADR 0004 (Python stack)
- **Deciders:** Mario (Nexoro Tech)

## Context

The draft service design (2026-07-09) proposed: compute-on-cache-miss with a
`REQUEST_PENDING` status and client polling; computed predictions stored **only** in Redis
with a 6-hour TTL; RabbitMQ for job dispatch **plus** Redis pub/sub for completion events; a
WebSocket server shipped in Phase 1 for a Phase 2 (Next.js) consumer.

The architecture review found the workload is small and fully enumerable — ~10–20 fixtures
per league per week, refreshed by a 6-hour ingestion cycle. That makes lazy computation the
wrong shape: it creates a polling UX, an unauthenticated cost-DoS surface (any request can
trigger paid Claude computation), unbounded AI spend, and — because results lived only in a
TTL'd cache — **no prediction history**, making model accuracy/calibration unmeasurable.

## Decision

1. **Precompute pipeline.** Ingestion (CronJob, every 6h) fetches football-data.org, upserts
   facts into Postgres, then publishes a "data ready" job. The calculation service consumes
   it, recomputes predictions + Claude (Haiku) narration for **all** upcoming fixtures in
   supported leagues, writes them to Postgres, and warms the Redis cache. API requests are
   plain reads — `REQUEST_PENDING` and client polling are removed from the design.
2. **Postgres is the system of record for predictions.** Rows are permanent and keyed by
   fixture + model version. This is product-critical: stored prediction history vs. actual
   results is the only way to measure calibration and defend value-bet claims.
3. **RabbitMQ is the single broker** for jobs and events. Redis pub/sub is dropped. RabbitMQ
   is over-capacity for one pipeline and is adopted **explicitly as a Kubernetes/messaging
   learning target**, on the ADR 0002 precedent — not as a structural requirement.
4. **Redis is a read-through cache only.** Removable without design change; kept partly as a
   stateful-workload learning artifact.
5. **Auth = static API keys** (`Authorization: Bearer <key>`), stored in Postgres, per-key
   rate limits. No `POST /auth` endpoint.
6. **WebSockets deferred to Phase 2** (Next.js client, live in-play features). With
   precompute there is no Phase 1 completion event to push. When built: FastAPI native WS.
7. **Endpoint set v2** (all `GET`, all key-authed):
   - `GET /v1/leagues` — supported leagues
   - `GET /v1/leagues/{league}/fixtures` — upcoming week's fixtures
   - `GET /v1/leagues/{league}/suggestions` — weekly suggestions, whole league
   - `GET /v1/leagues/{league}/teams/{team}/suggestions` — one team's suggestions
     (league-scoped: a bare team name is ambiguous across leagues)

## Consequences

- **Positive:** no polling or race conditions; Claude spend bounded and predictable
  (~10–20 Haiku calls per league per cycle ≈ pennies/day); p99 latency = one indexed read;
  the cost-DoS surface is gone; prediction history enables calibration reporting.
- **Negative:** data freshness is bounded by pipeline cadence (≤6h stale — acceptable for
  weekly fixtures); RabbitMQ and Redis are more infrastructure than the workload merits
  (accepted knowingly, as learning scope).
- **Neutral:** the Kubernetes learning surface is preserved — Deployments (api, calc),
  CronJob (ingestion), StatefulSets (RabbitMQ, Redis, Postgres), plus broker and cache
  operations.

## Amendments

- **2026-08-10 — Endpoint set reduced to one.** Decision 7's four endpoints become a single
  client entrypoint: `GET /v1/leagues/{league}/teams/{team}/suggestions`. The other three are
  deferred, not deleted. The response is the **commentary only** (preview prose +
  `suggested_bet` + reason) — the raw engine numbers stay in Postgres for calibration and are
  not part of the client contract. Read path: resolve team+league to the next fixture, then
  Redis (`prediction:{match_id}`, TTL expiring at kickoff, commentary only) → Postgres →
  `404`; never generation-on-request, per this ADR's original reasoning. Contract:
  `docs/api-spec.md` v3. Decisions 1–6 are untouched.
