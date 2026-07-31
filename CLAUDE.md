# A-Game

Football statistics and predictions app built on the football-data.org API: Elo + Poisson models produce match probabilities, and an AI layer (Claude Haiku) phrases them into previews and value-bet suggestions. Delivered as an **API-only service** — four read-only `GET` endpoints, no v1 frontend (ADR 0003/0005). Personal-use portfolio project; not intended for live clients (for now).

**Owner:** Mario (Nexoro Tech). Canonical repo lives in WSL2 Ubuntu at `~/repos/a-game`.

## Read these first
- `TECHSTACK.md` — fast-load stack reference (runtime, services, DB, IaC, commands). Authoritative for the stack.
- `docs/adr/` — decisions 0001–0005. Current state = **0002** (EKS prod target for learning), **0003** (API-only surface), **0004** (Python stack), **0005** (precompute architecture). ADR 0001 survives only in its Postgres/Terraform/Docker/Claude portions.
- `docs/api-spec.md` (v2) — the public HTTP contract.
- `docs/input-spec.md` — v1 input specification and the Elo/Poisson engine design table.
- `docs/system-design/` — layered architecture + flow diagrams (README has the reading order).

## What this is
- **Data source:** football-data.org free tier (matches, standings, scorers, teams, persons). Odds and shot-level xG are paid and out of v1 scope; injuries are v2.
- **Engine:** Elo + Poisson, computed deterministically in code. The AI layer only phrases numbers — it never invents stats.
- **Architecture (ADR 0005 — precompute, do not reopen):** worker CronJob (daily 06:00 UTC, ADR 0007) → Postgres upsert → RabbitMQ "data ready" job → brain recomputes ALL upcoming fixtures + Haiku narration → Postgres (permanent, model-versioned — calibration tracking) → warm Redis. The API does plain reads; no compute-on-request, no pending/polling states.
- **Roles:** Postgres = only system of record · Redis = read-through cache, removable · RabbitMQ = single broker, adopted as a learning target.

## Environment
- Local dev runs entirely on the laptop at **$0**: Docker + k3s (via k3d) + LocalStack (AWS emulation) + a Postgres container + Terraform pointed at LocalStack.
- Bring the data services up from the repo root: `docker compose up -d` (LocalStack on 4566, Postgres on 5432).
- The AWS CLI is wrapped by `awslocal` (targets `localhost:4566`). Terraform lives in `infrastructure/`, segmented by layer (`network/`, `app/`; `cluster/` planned) with remote S3 state in LocalStack.

## Non-negotiables
- **$0 local-first.** No AWS spend until a deliberate go-live; everything is emulated locally. (One planned exception: a single ephemeral real-EKS session ~$2, destroyed same day.)
- **No hardcoded secrets — and consume them the Kubernetes-native way.** Local `.env` (gitignored) in dev; AWS Secrets Manager in cloud; Claude API key server-side only. The app reads every secret from an env var or mounted file that a **Kubernetes Secret** populates — it must **never** call the AWS SDK to fetch secrets and **never** hardcode a secret name/ARN/path. This keeps the local→EKS seam clean: swapping a local `.env`-backed Secret for one synced by External Secrets (IRSA) later becomes a config change with **zero app-code change**. Do not build a secret-handling path that closes this seam.
- **Prod target = EKS, chosen for Kubernetes learning (ADR 0002).** Layered Terraform: network → cluster → app. The *final* prod-deployment decision is deferred until the app is built.
- **Approval before changes.** Summarize what/where/why and wait for explicit approval before changing code, config, infra, or docs.
- **Learning mode:** Mario runs all shell commands himself and hand-writes learning-track code (e.g. Terraform); Claude narrates, explains, and reviews.

## Build phasing
Order of work: local first, then production hardening — **do not start the production features until the local version is complete and tested.**
1. **Local ($0):** build and test the whole app on k3d + LocalStack with plain `.env`-backed Kubernetes Secrets. No IRSA / OIDC / External Secrets — they can't run on k3d (no real OIDC issuer).
2. **Production features (after local is done + tested):** add the prod-only identity/secrets layer — the OIDC provider, per-ServiceAccount IAM roles (IRSA), and External Secrets Operator syncing from AWS Secrets Manager. Written now only as design; **applied against a real, ephemeral EKS cluster** for a genuine end-to-end test (spin up → verify a pod pulls its secret with no static key → `terraform destroy` same day, ~$2).
The secret-consumption rule in Non-negotiables is what makes step 2 additive rather than a refactor. The detailed cluster runtime design is in `docs/system-design/cluster-runtime-view.svg` (prod-only elements marked "EKS only — design-only for now").

## Commands
- Python (once scaffolded): `uv run pytest` / `ruff check .` / `ruff format .` / `mypy src/`
- Cluster: `k3d cluster start a-game` (API pinned to `127.0.0.1:6550`)
- Data services: `docker compose up -d` · verify LocalStack: `awslocal s3 ls`
- Terraform: `terraform -chdir=infrastructure/<layer> plan|apply` (endpoints → LocalStack)

## Structure
- `docs/` — ADRs, api-spec (v2), input-spec, system-design diagrams, prompts.
- `infrastructure/` — layered Terraform roots: `network/` (in progress), `app/` (S3 state working); `cluster/` planned.
- `k8s/` — hand-written learning manifests for the local k3d cluster (numbered: namespace, serviceaccounts, Postgres, Redis, RabbitMQ).
- `a-game-api/` — throwaway FastAPI hello-world experiment; **not** the real scaffold.
- `localstack/init/ready.d/` — LocalStack init hooks (recreates the tfstate bucket; community LocalStack is ephemeral).
- App scaffold (Python/FastAPI: api + brain + worker services, shared pydantic models, `uv` layout) — **not started yet** (Track B: infra-first). DB schema design comes first, shaped by the calibration requirement.
