# A-Game — System design diagrams

Current as of **2026-07-30**, reflecting ADR 0002 (EKS for learning), ADR 0003 (API-only
SaaS), ADR 0004 (Python stack), ADR 0005 (precompute architecture), ADR 0007 (daily,
change-gated ingestion), ADR 0008 (AI platform layer), and ADR 0009 (local validation split —
EKS Terraform is plan-only, Kubernetes work runs on local k3s). Open any `.svg` in a browser or
VS Code; the `.html` diagrams open in a browser.

## Reading order

| # | File | What it shows |
|---|---|---|
| 1 | [`a-game-architecture.svg`](a-game-architecture.svg) | The static layered view — every component and what depends on what |
| 2 | [`flow-1-precompute-pipeline.svg`](flow-1-precompute-pipeline.svg) | The daily pipeline that creates **all** data: worker → RabbitMQ → brain → Claude → Postgres/Redis |
| 3 | [`flow-2-api-request.svg`](flow-2-api-request.svg) | What happens on every API call: key auth → cache → Postgres. Plain reads, no computation, no pending states |
| 4 | [`flow-3-phase2-websocket.svg`](flow-3-phase2-websocket.svg) | **Phase 2 only** (not built in v1): how push updates will reach the Next.js client via RabbitMQ |
| 5 | [`k8s-deployment-view.svg`](k8s-deployment-view.svg) | How the pieces map to Kubernetes workloads: Deployments (api, brain), CronJob (worker), StatefulSets (RabbitMQ, Redis, Postgres). Validated locally on **k3s**, not EKS (ADR 0009) |
| 6 | [`network-topology.html`](network-topology.html) | The AWS network layer: how a public ALB in the public subnets routes inbound traffic to api pods in the private subnets across two AZs, plus route tables and NAT egress |
| 7 | [`ai-platform-topology.html`](ai-platform-topology.html) | **The AI platform layer** (ADR 0008): LiteLLM gateway as the one LLM egress hop — two provider routes, Claude Haiku primary with OpenAI as its fallback, a third self-hosted CPU-scale route planned — plus Langfuse tracing, pgvector RAG, a tool-using agent, and Argo (orchestration fork open — see the doc) as private-subnet workloads. The **flagship deliverable is the CI eval gate** (golden dataset, fact-checker, LLM-as-judge, regression block — 2026-08-05). See [`ai-platform.md`](ai-platform.md) for the full design |
| 8 | [`irsa-flow.html`](irsa-flow.html) | **IRSA** — how a pod trades a Kubernetes service account token for its own temporary IAM credentials: the OIDC issuer, the STS exchange, and why the node role isn't good enough. Also disambiguates the three certificates involved |
| 9 | [`langfuse-trace-network.html`](langfuse-trace-network.html) | **The Langfuse trace path** (ADR 0008 Tier 1): how one trace moves from the LiteLLM gateway through web, MinIO, the Redis queue and the worker into ClickHouse — plus a table of every edge and the NetworkPolicy each one needs, both sides |
| 10 | [`k3d-to-eks-delta.md`](k3d-to-eks-delta.md) | **What changes on EKS** — the four things that fail silently (NetworkPolicy not enforced, `local-path` missing, hand-created Secrets, static keys → IRSA), the component swaps, and a per-file summary. Written while the reasoning behind each local value was still fresh |

## The design in three sentences

The worker CronJob pulls football-data.org daily at 06:00 UTC and upserts raw facts into
Postgres; **if that upsert changed anything** (ADR 0007) a RabbitMQ job then triggers the
brain to recompute Elo + Poisson
predictions and Claude-written previews for **all** upcoming fixtures, storing them
permanently (model-versioned) in Postgres and warming Redis. The FastAPI service serves four
authenticated `GET` endpoints as plain reads of that precomputed data — it never computes
anything per request. Postgres is the only system of record; Redis is the app's cache — it
*was* removable without design change, but stops being so once AI-platform Tier 1 lands, since
Langfuse uses it as a queue (ADR 0008 §Amendments, 2026-07-30); RabbitMQ is deliberately
adopted as a learning target.

## Related documents

- [`../api-spec.md`](../api-spec.md) — the public HTTP contract (v2)
- [`../input-spec.md`](../input-spec.md) — engine inputs, the external endpoints used (§2–5), output contract (§8)
- [`../schema.md`](../schema.md) — the Postgres schema (ingestion half: team, competition, season, match)
- [`../adr/`](../adr/) — decision records 0001–0009
- [`../../TECHSTACK.md`](../../TECHSTACK.md) — fast-load stack reference

## Maintenance

Diagrams 1–5 are hand-maintained SVG; `network-topology.html`, `ai-platform-topology.html`,
`irsa-flow.html` and `langfuse-trace-network.html` are self-contained HTML pages (inline CSS,
theme-aware, no external assets) — open any of them in a browser. Any ADR that changes the
architecture must update the affected diagrams **in the same change**.
