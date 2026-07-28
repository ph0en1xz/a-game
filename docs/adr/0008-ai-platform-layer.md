# ADR 0008 — AI platform layer: gateway, observability, evals, RAG, orchestration

- **Status:** Accepted
- **Date:** 2026-07-22
- **Amends:** ADR 0005 — its precompute pipeline stands, but the calc service no longer calls
  the Anthropic API directly (§Decision 1 below); the Claude call now goes through an
  in-cluster model gateway.
- **Related:** ADR 0005 (precompute pipeline), ADR 0007 (change-gated ingestion), ADR 0002
  (EKS-for-learning precedent), ADR 0004 (Python stack)
- **Deciders:** Mario (Nexoro Tech)

## Context

A-Game already has the two halves an AI-platform story needs: a **numerical model** (Elo +
Poisson) and an **LLM** (Claude Haiku previews). Today both are inline function calls —
`commentary.py` calls the Anthropic API directly from the calc service. That is an
*application* using AI, not a *platform* operating it.

The project's owner is targeting an **AI infrastructure engineer** role. The gap between the
current state and that role is not more model code — it is the operational platform *around*
the models: a controlled egress path, per-request cost/latency/token observability,
automated output evaluation, retrieval grounding, and workflow orchestration. Those are the
artifacts that demonstrate "I operate LLM workloads in production."

This is a deliberate scope expansion of a learning/portfolio project (not going live), so the
selection is weighted by **signal-per-dollar**: prefer additions that are pure infra, run as
ordinary cluster workloads, and cost little or nothing locally over ones that need GPUs.

## Decision

Add an **AI platform layer** to the cluster, in three tiers by priority. Sequenced **after**
the Terraform infra track (network → cluster → app) completes.

### Tier 1 — v1 (cheap, pure infra, do first)

1. **Model gateway — LiteLLM proxy.** A single in-cluster Deployment + Service that every
   Claude call routes through. Owns rate limiting, budget caps, response caching, retries,
   and model fallback. The calc service calls the gateway's cluster-internal address; **only
   the gateway holds the Anthropic credential and is the only workload with egress to
   `api.anthropic.com:443`.** This supersedes the direct-call assumption in ADR 0005 and
   collapses LLM egress to one controlled hop (feeds the egress NetworkPolicy work).
2. **LLM observability — self-hosted Langfuse.** Traces every prediction+preview: tokens,
   cost, latency, prompt, response, model version. Runs as a container with its own Postgres.
   The gateway emits traces to it.
3. **Eval harness gated in CI.** Automated checks on LLM output — structured-output
   validation, factuality against the real match stats, and regression on a fixed fixture
   set — run in GitHub Actions and block merge on regression (extends ADR 0006's pipeline).

### Tier 2 — next

4. **pgvector RAG grounding.** Enable the `vector` extension on the existing Postgres;
   retrieve similar historical matches to ground each Haiku preview instead of free-
   generating. Demonstrates the retrieval-infra pattern with no new datastore.
5. **Workflow orchestration — Argo Workflows.** Adds retries, backfills, and a run UI to the
   daily pipeline. **Open design fork, to settle when Tier 2 starts** — the current pipeline
   is event-driven (a daily CronJob runs ingestion; ingestion publishes a change-gated
   RabbitMQ "data ready" event; the calc service is a long-running consumer that reacts). Argo
   can enter one of two ways, not both:
   - **(A) Scheduler only —** Argo replaces just the ingestion CronJob (the one daily tick at
     06:00 UTC). Everything downstream stays event-driven through RabbitMQ; calc keeps reacting
     to the event. Smallest change; keeps the messaging learning surface intact.
   - **(B) Full DAG —** Argo runs ingest → calc → store as explicit sequential steps. RabbitMQ
     loses its *trigger* role for this flow (it stays only as the standalone messaging learning
     target). Biggest orchestration signal; but it removes the event-driven decoupling.

   Nothing here polls or triggers calc on a timer either way — calc is never "run every N
   hours." The cadence is daily and change-gated (ADR 0007); Argo touches *how the run is
   orchestrated*, not how often calc computes.

### Tier 3 — document-only (design, do not run)

6. **Self-hosted inference — vLLM / KServe on a GPU node group.** The deepest AI-infra
   signal, but GPU nodes cost real money. Designed in full (ADR + Terraform for the GPU
   nodegroup, taints/tolerations, autoscaling) and provable with a tiny model for a few
   minutes, but **not left running.** Kept as a showpiece design, not a standing workload.

## Consequences

- **Positive:** the project demonstrates the full operational surface of an LLM platform —
  controlled egress, cost/latency observability, automated evals, retrieval grounding, and
  orchestration — which is precisely the AI-infra role signal. Tier 1 is all cheap cluster
  workloads with no GPU.
- **Positive (security/networking):** routing all LLM traffic through the gateway means
  exactly one workload needs egress to Anthropic and exactly one holds the credential —
  tighter NetworkPolicy and IRSA scope than N services each calling out.
- **Negative:** more moving parts on the cluster (LiteLLM, Langfuse + its DB, Argo
  controller) — more to deploy, secure, and reason about. Accepted: that operational surface
  *is* the thing being demonstrated.
- **Negative:** Tier 3 GPU serving is expensive and stays document-only; the portfolio shows
  the design, not a running GPU workload.
- **Neutral:** the numerical model (Elo/Poisson) and the ingestion cadence (ADR 0007) are
  unchanged. This layer wraps the existing LLM call; it does not change what gets predicted.
- **Follow-up:** per-component ADRs may be written when each is built (gateway config, eval
  criteria, Argo DAG shape). `TECHSTACK.md`, `docs/system-design/README.md`, and the
  architecture diagrams are updated as each tier lands — not preemptively.
