# A-Game — Tech Stack

> Fast-load stack reference for agents/skills. **Read this first.**

**Project:** A-Game — football statistics, predictions (Elo + Poisson), AI-generated match previews, and value-bet suggestions, delivered as an API-only SaaS on football-data.org data.
**Owner:** Mario (Nexoro Tech)
**Repo:** ~/repos/a-game (WSL2 Ubuntu, canonical; Windows-side copy deprecated)
**Status:** Pre-build · Python stack (ADR 0004, 2026-07-09) · prod target = EKS for learning (ADR 0002); final prod deferred

---

## Product surface
- **API-only SaaS — no frontend in v1** (ADR 0003; Next.js browser client is Phase 2).
- **Four read-only `GET` endpoints** (ADR 0005): leagues list, league fixtures, league weekly suggestions, team suggestions. Full contract in `docs/api-spec.md` (v2).
- **Auth: static API keys** (`Authorization: Bearer`), stored hashed in Postgres, per-key rate limits. No auth endpoint.

## Runtime & language
- **Python 3.12+** (ADR 0004 — supersedes ADR 0001's Node/TS).
- **API service:** FastAPI + **pydantic** (validation at every external boundary — HTTP, football-data payloads, Claude responses).
- **Workers:** plain Python (brain, worker), sharing the same pydantic models.
- **Tooling:** `ruff` (lint + format) · `mypy` · `pytest` · **`uv`** for deps/envs.
- Core logic (data client, Elo/Poisson engine, AI layer) stays **portable, framework-agnostic modules** — no FastAPI imports in the core.

## Services (v1 architecture — ADR 0005)
- **api** — FastAPI REST; plain reads of precomputed predictions. WebSockets deferred to Phase 2.
- **worker** — k8s CronJob daily at 06:00 UTC (ADR 0007): fetch football-data.org → clean/transform → **upsert** facts to Postgres → publish "data ready" job **only if the upsert changed rows**. A no-change run publishes nothing and exits 0.
- **brain** — consumes the job; recomputes predictions + Claude (Haiku) narration for **all** upcoming fixtures; writes Postgres; warms Redis.
- **Precompute model:** no compute-on-request, no pending/polling states (ADR 0005).
- **ServiceAccounts** (`k8s/10-serviceaccounts.yaml`) — one per service, the IRSA anchor for prod: `a-game-api-sa`, `a-game-brain-sa`, `a-game-worker-sa`. Token automount is disabled on all three (none call the k8s API). *Naming note (2026-07-30): docs and diagrams previously called these services api/calc/ingestion while the pods were named api/brain/worker. Everything now uses **api · brain · worker** — the pod names — so no mapping is needed. "Ingestion" and "calculation" still appear where they mean the **activity**, not the service.*

## Database & cache
- **PostgreSQL — the only system of record.** Raw facts (JSONB for payloads) **and predictions** (permanent, model-versioned → enables accuracy/calibration tracking). MongoDB dropped (ADR 0001).
- **Redis — read-through cache for the app** (no pub/sub). Was "removable without design change"; **no longer true once AI-platform Tier 1 lands** — Langfuse uses Redis as a *queue*, on a dedicated `REDIS_DB` index, so deleting Redis then breaks trace ingestion (ADR 0008 §Amendments, 2026-07-30). Still a deliberate stateful-workload learning artifact.

## Messaging
- **RabbitMQ — single broker** for jobs and events. Adopted explicitly as a **learning target** (ADR 0005, on the ADR 0002 precedent) — not a structural requirement.

## AI layer
- **All model calls go through the in-cluster LiteLLM gateway** (ADR 0008) — never a provider SDK pointed at the internet. The brain uses the **`openai` client library** against the gateway's cluster-internal Service, because LiteLLM speaks the OpenAI wire format; `anthropic` comes out of `a-game-brain/pyproject.toml` and `openai` goes in. The application asks for a model *alias* and holds no provider credential.
- **Two providers behind the gateway** (2026-08-04): **Claude Haiku** (`claude-haiku` → `anthropic/claude-haiku-4-5-20251001`) as the primary route for previews / value-bet narration, with **OpenAI `gpt-4o-mini`** configured as its fallback. Swapping or adding a provider is a gateway ConfigMap change, not an application change.
- Math stays **deterministic** (Elo/Poisson in code); AI only phrases the numbers, never invents stats — and that promise is **enforced as a CI assertion** by the eval harness, the layer's flagship deliverable (golden dataset, fact-checker, LLM-as-judge, regression gate; ADR 0008, 2026-08-05). Spend is bounded by precompute (~10–20 calls per league per cycle) and, per ADR 0007, cycles only fire when upstream data actually changed.
- **Planned additions (decided 2026-08-05, none built):** model-comparison report per gateway route; tool-using match-analyst agent (Tier 2); self-hosted ~3B model as a third gateway route at CPU scale (Tier 3 partial — GPU stays doc-only); AI threat-model doc; per-route budget caps + cost alert. Non-goals: fine-tuning, chatbot/UI, semantic caching.

## Infrastructure as Code
- **Terraform.** Run against **LocalStack** locally ($0); real AWS at deploy time. State segmented by layer + env (see `infrastructure/`).

## Orchestration & packaging
- **Docker** — multi-stage, non-root.
- **Kubernetes — the compute model end to end:** **k3s** (via k3d) locally, **EKS** as the prod target. Deliberate: the priority is hands-on Kubernetes experience. Workloads: Deployments (api, brain), CronJob (worker), StatefulSets (RabbitMQ, Redis, Postgres).

## CI/CD (ADR 0006, 2026-07-10)
- **GitHub Actions** — CI (ruff · mypy · pytest · Docker build) once app code exists; `terraform plan` / gated `apply` via **OIDC** (no static AWS keys).
- **Local deploys:** `kubectl apply` to k3d. **GitOps (Argo CD)** deferred to the EKS/prod phase (needs a persistent cluster to reconcile against).

## Prod execution model (ADR 0002, 2026-07-08 — supersedes ADR 0001)
- **EKS as the working prod target**, chosen to maximize Kubernetes learning. Layered Terraform: **network** (VPC/subnets) → **cluster** (EKS control plane, node groups, IRSA) → **app** (workloads, app IAM, RDS, S3).
- **Final prod-deployment decision is deferred** to when the app is built and ready.
- Prod Postgres host **TBD** (RDS vs external) — see Still open.

## Cloud
- **AWS.** Local dev fully emulated via **LocalStack** (AWS APIs, $0).

## External integrations
- **football-data.org API (free tier)** — matches, standings, scorers, teams, persons. Covers all prediction/analytics inputs (endpoints enumerated in `docs/input-spec.md` §2–5).
- **Odds feed** (paid) — required for value bets; `betting` is `null` until wired.
- **Injury source** (v2, external API/scrape) — deferred.

## Hard constraints
- **Local-first, $0 build:** the entire stack runs on the laptop (k3s + LocalStack + containers). **No AWS spend until deliberate go-live.**
- **Secrets never hardcoded** — local `.env` (gitignored) in dev, AWS Secrets Manager in cloud. Claude API key server-side only.
- Free football API covers everything except **odds** and **shot-level xG** (both paid).

## Key local-dev commands (finalize at scaffold)
- Local cluster: `k3d cluster create a-game`
- LocalStack + Postgres: `docker compose up -d` (repo root)
- Terraform (local, per layer): `terraform -chdir=infrastructure/<layer> apply` (endpoints → LocalStack)
- S3 check: `awslocal s3 ls`
- Python: `uv run pytest` / `ruff check .` / `ruff format .` / `mypy src/`

---

## Decisions
See `docs/adr/` for decisions, `docs/input-spec.md` for engine detail, `docs/api-spec.md` (v2) for the API contract, `docs/schema.md` for the database schema, `docs/system-design/` for the diagram.
- ✅ **Prediction engine** — PL only at launch; ratings keyed by team+competition; 3-season
  window, 6-month decay, K=20 with damped GD scaling, +70 HFA, promoted teams seed at league
  average − penalty, 5–6 match min sample; probabilities come from the Poisson matrix, Elo is
  rating state + cross-check; Brier/log-loss gate on walk-forward backtest vs fixed baselines;
  CL, congestion, and player ratings deferred. **ADR 0010**, 2026-08-07.
- ✅ **Local validation split** — EKS Terraform stays plan-only (LocalStack Community has no EKS); Kubernetes learning runs on local k3s via k3d. **ADR 0009**, 2026-07-28.
- ✅ **AI platform layer** — LiteLLM gateway, self-hosted Langfuse, CI eval gate (Tier 1); pgvector RAG + Argo (Tier 2); GPU serving doc-only (Tier 3). **ADR 0008**, 2026-07-22, amended 2026-07-30 (Langfuse is six components; Tier 1 runs before the infra track), 2026-08-04 (second provider + fallback chain), and 2026-08-05 (evals become the flagship; model-comparison report; tool-using agent; self-hosted CPU-scale model route; AI threat model; non-goals fixed).
- ✅ **Ingestion cadence** — daily at 06:00 UTC; "data ready" published only when the upsert changed rows; 3-season backfill is a one-off bootstrap. **ADR 0007**, 2026-07-16 (amends ADR 0005's 6h cadence).
- ✅ **CI/CD** — GitHub Actions (CI + Terraform via OIDC); local `kubectl apply`; GitOps (Argo CD) deferred to EKS/prod phase. **ADR 0006**, 2026-07-10.
- ✅ **v1 service architecture** — precompute pipeline; Postgres = prediction record; RabbitMQ single broker (learning); Redis cache-only; API keys; WS → Phase 2. **ADR 0005**, 2026-07-09 (amends ADR 0003's endpoint set).
- ✅ **Language: Python** (FastAPI/pydantic/uv). **ADR 0004**, 2026-07-09 (supersedes ADR 0001's Node/TS).
- ✅ **Product surface** — API-only SaaS, no v1 frontend. **ADR 0003**, 2026-07-09 (endpoint set since amended by 0005).
- ✅ **Prod execution model** — EKS as the learning prod target; final prod deferred. **ADR 0002**, 2026-07-08 (supersedes ADR 0001's serverless call).
- ✅ **ADR 0001** — original stack (Postgres/Terraform/Docker/Claude portions stand; language superseded by 0004; prod portion by 0002).

### Still open
1. **Database schema — ingestion half drafted** (`docs/schema.md`, 2026-07-16): `team`, `competition`, `season`, `match`. **Predictions still unmodelled** — shaped by the calibration requirement (permanent, model-versioned — ADR 0005) and designed with the engine. Also open: migration tooling (numbered `.sql` first, Alembic later — no ORM), enum-vs-CHECK, indexes.
2. ~~6 engine design decisions~~ — **locked in ADR 0010** (2026-08-07), along with four more
   the original list missed: K-factor, home advantage, Elo↔Poisson relationship, draw source.
3. **Prod Postgres host** — RDS vs external. Decide before prod IaC.
4. **Final prod-deployment decision** — deferred until the app is ready (EKS vs serverless vs other).
5. **Scaffolding** — not yet started (Track B: infra-first).
