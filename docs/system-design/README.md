# A-Game — System design diagrams

Current as of **2026-07-16**, reflecting ADR 0002 (EKS for learning), ADR 0003 (API-only
SaaS), ADR 0004 (Python stack), ADR 0005 (precompute architecture), and ADR 0007 (daily,
change-gated ingestion). Open any `.svg` in a browser or VS Code.

## Reading order

| # | File | What it shows |
|---|---|---|
| 1 | [`a-game-architecture.svg`](a-game-architecture.svg) | The static layered view — every component and what depends on what |
| 2 | [`flow-1-precompute-pipeline.svg`](flow-1-precompute-pipeline.svg) | The daily pipeline that creates **all** data: ingest → RabbitMQ → calc → Claude → Postgres/Redis |
| 3 | [`flow-2-api-request.svg`](flow-2-api-request.svg) | What happens on every API call: key auth → cache → Postgres. Plain reads, no computation, no pending states |
| 4 | [`flow-3-phase2-websocket.svg`](flow-3-phase2-websocket.svg) | **Phase 2 only** (not built in v1): how push updates will reach the Next.js client via RabbitMQ |
| 5 | [`k8s-deployment-view.svg`](k8s-deployment-view.svg) | How the pieces map to Kubernetes workloads: Deployments (api, calc), CronJob (ingestion), StatefulSets (RabbitMQ, Redis, Postgres) |

## The design in three sentences

The ingestion CronJob pulls football-data.org daily at 06:00 UTC and upserts raw facts into
Postgres; **if that upsert changed anything** (ADR 0007) a RabbitMQ job then triggers the
calc service to recompute Elo + Poisson
predictions and Claude-written previews for **all** upcoming fixtures, storing them
permanently (model-versioned) in Postgres and warming Redis. The FastAPI service serves four
authenticated `GET` endpoints as plain reads of that precomputed data — it never computes
anything per request. Postgres is the only system of record; Redis is a removable cache;
RabbitMQ is deliberately adopted as a learning target.

## Related documents

- [`../api-spec.md`](../api-spec.md) — the public HTTP contract (v2)
- [`../input-spec.md`](../input-spec.md) — engine inputs, the external endpoints used (§2–5), output contract (§8)
- [`../schema.md`](../schema.md) — the Postgres schema (ingestion half: team, competition, season, match)
- [`../adr/`](../adr/) — decision records 0001–0007
- [`../../TECHSTACK.md`](../../TECHSTACK.md) — fast-load stack reference

## Maintenance

These diagrams are hand-maintained SVG. Any ADR that changes the architecture must update
the affected diagrams **in the same change**.
