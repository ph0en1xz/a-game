# ADR 0001 — Tech stack and production execution model

- **Status:** Accepted — prod-execution portion **superseded by ADR 0002** (2026-07-08); language/runtime portion **superseded by ADR 0004** (2026-07-09); remaining stack decisions (Postgres-only, Claude AI layer, Terraform, Docker) still stand
- **Date:** 2026-07-08
- **Deciders:** Mario (Nexoro Tech)

## Context

A-Game is a football statistics and predictions app built on the football-data.org API: scheduled data pulls, batch computation of Elo and Poisson models, an API serving predictions, and an AI layer that phrases the numbers into previews and value-bet suggestions. Traffic is low and bursty; most work is scheduled (cron) rather than continuous.

Priorities, in order:
1. **$0 local-first build** — the whole stack runs on the laptop with no cloud spend until a deliberate go-live.
2. **Minimal prod cost** — pay near nothing while traffic is low.
3. **Learn Kubernetes** — the local environment is deliberately k3s so the owner learns k8s hands-on.

## Decision

**Stack**
- **Language/runtime:** Node.js + TypeScript (strict). Core logic (data client, Elo/Poisson engine, AI layer) written as portable, framework-agnostic modules so the same code runs in a container or a Lambda handler.
- **Database:** PostgreSQL only, with JSONB columns for raw API payloads. MongoDB dropped for v1.
- **AI layer:** Claude (Haiku for bulk generation). Math stays deterministic in code; the model only phrases numbers, never invents stats.
- **IaC:** Terraform, run against LocalStack locally and real AWS in prod.
- **Packaging:** Docker (multi-stage, non-root). The same image is the Lambda container image in prod.

**Local development**
- Docker + k3s (via k3d) + LocalStack for AWS emulation + a Postgres container, all $0. k3s is kept for learning and local runs.

**Production execution model: serverless**
- **Lambda container image** (the same image built for local) for compute.
- **EventBridge Scheduler** for the cron data pulls.
- **API Gateway to Lambda** for the API surface.
- Prod Postgres host is left open (RDS vs Supabase), to be decided before prod IaC.

## Alternatives considered

- **EKS + Helm (Track B).** Kubernetes in prod too, giving full local/prod parity and one deploy model that carries the local manifests straight through. Rejected for cost and ops: an EKS control plane runs ~$73/month plus nodes, 24/7, even while idle — at odds with the minimal-cost priority for a small, bursty workload.

## Consequences

- **Positive:** ~$0 prod when idle, scales to zero, low ops burden. Local k8s learning goal is preserved independently of the prod choice. One Docker image bridges local and prod.
- **Negative:** Two deploy definitions — local k8s manifests vs Lambda/Terraform prod. The local **k8s manifests become a learning artifact, not a prod deploy path**. Lambda cold starts and a 15-minute execution cap apply (acceptable for these scheduled jobs).
- **Follow-up:** decide the prod Postgres host (RDS vs Supabase); revisit if the workload stops being bursty.
