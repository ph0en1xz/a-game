# A-Game — System design diagrams

Current as of **2026-07-22**, reflecting ADR 0002 (EKS for learning), ADR 0003 (API-only
SaaS), ADR 0004 (Python stack), ADR 0005 (precompute architecture), ADR 0007 (daily,
change-gated ingestion), and ADR 0008 (AI platform layer). Open any `.svg` in a browser or
VS Code; the `.html` diagrams open in a browser.

## Reading order

| # | File | What it shows |
|---|---|---|
| 1 | [`a-game-architecture.svg`](a-game-architecture.svg) | The static layered view — every component and what depends on what |
| 2 | [`flow-1-precompute-pipeline.svg`](flow-1-precompute-pipeline.svg) | The daily pipeline that creates **all** data: ingest → RabbitMQ → calc → Claude → Postgres/Redis |
| 3 | [`flow-2-api-request.svg`](flow-2-api-request.svg) | What happens on every API call: key auth → cache → Postgres. Plain reads, no computation, no pending states |
| 4 | [`flow-3-phase2-websocket.svg`](flow-3-phase2-websocket.svg) | **Phase 2 only** (not built in v1): how push updates will reach the Next.js client via RabbitMQ |
| 5 | [`k8s-deployment-view.svg`](k8s-deployment-view.svg) | How the pieces map to Kubernetes workloads: Deployments (api, calc), CronJob (ingestion), StatefulSets (RabbitMQ, Redis, Postgres) |
| 6 | [`network-topology.html`](network-topology.html) | The AWS network layer: how a public ALB in the public subnets routes inbound traffic to api pods in the private subnets across two AZs, plus route tables and NAT egress |
| 7 | [`ai-platform-topology.html`](ai-platform-topology.html) | **The AI platform layer** (ADR 0008): LiteLLM gateway as the one LLM egress hop, Langfuse tracing, pgvector RAG, and Argo (orchestration fork open — see the doc) as private-subnet workloads, plus a CI eval gate in GitHub Actions. See [`ai-platform.md`](ai-platform.md) for the full design |

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

Diagrams 1–5 are hand-maintained SVG; `network-topology.html` and `ai-platform-topology.html`
are self-contained HTML pages (inline CSS, theme-aware, no external assets) — open any of them
in a browser. Any ADR that changes the architecture must update the affected diagrams **in the
same change**.
