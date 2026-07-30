# ADR 0008 — AI platform layer: gateway, observability, evals, RAG, orchestration

- **Status:** Accepted — **amended 2026-07-30** (see §Amendments)
- **Date:** 2026-07-22
- **Amends:** ADR 0005 — its precompute pipeline stands, but the calc service no longer calls
  the Anthropic API directly (§Decision 1 below); the Claude call now goes through an
  in-cluster model gateway.
- **Related:** ADR 0005 (precompute pipeline), ADR 0007 (change-gated ingestion), ADR 0002
  (EKS-for-learning precedent), ADR 0004 (Python stack), ADR 0009 (local validation split)
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
<!-- Superseded 2026-07-30: Tier 1 now runs BEFORE the infra track finishes. See §Amendments. -->


### Tier 1 — v1 (cheap, pure infra, do first)

1. **Model gateway — LiteLLM proxy.** A single in-cluster Deployment + Service that every
   Claude call routes through. Owns rate limiting, budget caps, response caching, retries,
   and model fallback. The calc service calls the gateway's cluster-internal address; **only
   the gateway holds the Anthropic credential and is the only workload with egress to
   `api.anthropic.com:443`.** This supersedes the direct-call assumption in ADR 0005 and
   collapses LLM egress to one controlled hop (feeds the egress NetworkPolicy work).
2. **LLM observability — self-hosted Langfuse.** Traces every prediction+preview: tokens,
   cost, latency, prompt, response, model version. The gateway emits traces to it.
   Deployment shape and datastores: see §Amendments (2026-07-30). This item originally read
   "runs as a container with its own Postgres" — that was written against Langfuse v2 and no
   longer holds; current Langfuse needs six components.
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
- **Negative:** more moving parts on the cluster. Under the 2026-07-30 amendment that is
  LiteLLM, `langfuse-web`, `langfuse-worker`, ClickHouse and MinIO for Tier 1, plus the Argo
  controller at Tier 2 — five new workloads before orchestration, not two. More to deploy,
  secure, and reason about. Accepted: that operational surface *is* the thing being demonstrated.
- **Negative (cost):** the "costs little or nothing locally" framing in Context applies to the
  *infrastructure*, not the model calls. Every preview spends real Anthropic money. Small at this
  volume, but it is the reason the gateway's budget cap is a Tier 1 feature rather than a
  nice-to-have — set it before the first call, not after the first bill.
- **Negative:** Tier 3 GPU serving is expensive and stays document-only; the portfolio shows
  the design, not a running GPU workload.
- **Neutral:** the numerical model (Elo/Poisson) and the ingestion cadence (ADR 0007) are
  unchanged. This layer wraps the existing LLM call; it does not change what gets predicted.
- **Follow-up:** per-component ADRs may be written when each is built (gateway config, eval
  criteria, Argo DAG shape). `TECHSTACK.md`, `docs/system-design/README.md`, and the
  architecture diagrams are updated as each tier lands — not preemptively.

## Amendments

### 2026-07-30 — corrected premise: neither half of the Context's "already has" claim is true

The Context section opens with "A-Game already has the two halves an AI-platform story needs: a
**numerical model** (Elo + Poisson) and an **LLM** (Claude Haiku previews) … `commentary.py` calls
the Anthropic API directly from the calc service." The header's **Amends** line repeats it ("the
calc service no longer calls the Anthropic API directly"). **Neither half was ever true.** As of
2026-07-30, verified against the code:

- there is no `commentary.py` anywhere in `a-game-brain/app/` — the only files are
  `config.py`, `consumer.py`, `db.py`, `handlers.py`, `main.py`, `stores.py`, `__version__.py`;
- `anthropic>=0.116.0` is declared in `a-game-brain/pyproject.toml` but never imported anywhere;
- **the numerical model does not exist either.** `handlers.py` fetches the match row, writes
  `calc:last_match` to Redis, and logs `(stub)`. Its TODO reads: "swap this for real Elo + Poisson
  compute + result writes once the predictions schema exists." There is no Elo, no Poisson, and no
  predictions table.

So the honest framing is: this ADR designs a platform around two components that are both still
unwritten. That does not change the decision — but it changes what "wrapping the existing LLM
call" means, and the Consequences bullet claiming this layer "wraps the existing LLM call" should
be read as "will wrap the LLM call, which Tier 1 also introduces."

For Decision 1 this is strictly easier: there is no direct call to migrate, so **the gateway is
the only path to a model from the first line of code ever written.** The "supersedes the
direct-call assumption in ADR 0005" framing stands as a statement about ADR 0005's design intent,
not about existing code.

It does change sequencing. Tier 1 as written would produce a gateway with no traffic, Langfuse
with no traces, and an eval harness with no outputs. So Tier 1 gains a small application item: a
thin `commentary.py` in the brain that asks Haiku for a one-paragraph match preview from the
match row and validates the response with pydantic. It deliberately does **not** wait for real
Elo + Poisson output — a match row is enough to generate real traffic through the platform. The
full engine stays in the app-correctness track.

### 2026-07-30 — Langfuse datastores: six components, not one (option C)

Self-hosted Langfuse (v3, current) is not a single container. It requires:

| Component | Role |
|---|---|
| Postgres | transactional data |
| ClickHouse | traces, observations, scores (OLAP) |
| Redis/Valkey | queue **and** cache |
| S3-compatible blob storage | raw events, multi-modal inputs, large exports |
| `langfuse-web` | UI + API |
| `langfuse-worker` | async event processing |

ClickHouse is required, not a configurable backend — Postgres cannot be substituted for it. Note
this is unrelated to Tier 2's pgvector work: pgvector does similarity search over embeddings,
ClickHouse does columnar aggregation over trace rows. Both end up in the project doing different
jobs, and adopting pgvector does not reduce the Langfuse footprint.

**Decision — option C: self-host, reusing existing stateful workloads where possible.**

- **Reuse** the existing `a-game-postgres` StatefulSet (dedicated database + user for Langfuse).
- **Reuse** the existing `a-game-redis` StatefulSet (dedicated `REDIS_DB` index).
- **Add** ClickHouse, MinIO, `langfuse-web`, `langfuse-worker` — four new workloads instead of six.

Options rejected: **Langfuse Cloud** (zero infra, but the operational surface being demonstrated
would run on someone else's cluster — that surface *is* the point of this ADR); **full self-host
with all-new datastores** (cleanest isolation, but roughly doubles what runs on a laptop, and
ClickHouse alone wants ~2GB); **dropping Langfuse for LiteLLM's built-in Postgres logging**
(cheapest, but discards the observability signal this tier exists to produce).

**Accepted costs — both real, both deliberate:**

1. **Coupling.** Pointing a third-party tool at the application's own Postgres and Redis is not
   something to do in production. It is accepted here because this is a learning cluster and the
   isolation argument does not yet earn its resource cost. On EKS this becomes an RDS/ElastiCache
   decision and the two should separate.
2. **Redis stops being removable.** ADR 0005 and `TECHSTACK.md` describe Redis as a read-through
   cache that could be deleted without a design change. Langfuse uses Redis as a **queue**, so
   once Langfuse is wired up, deleting Redis breaks trace ingestion. `TECHSTACK.md` is corrected
   in the same change as this amendment.

**Positive side effect:** MinIO locally standing in for S3 on EKS mirrors the LocalStack
substitution already in use for AWS APIs (ADR 0009), so the blob-storage dependency strengthens
the local/prod portability story rather than adding a loose end.

### 2026-07-30 — sequencing changed: Tier 1 runs before the infra track finishes

The Decision section says this layer is "sequenced **after** the Terraform infra track (network →
cluster → app) completes." **That no longer holds, and waiting for it would block indefinitely.**

Why it can't be satisfied as written: the network layer is applied, but ADR 0009 makes the cluster
layer **plan-only** — LocalStack Community has no EKS, so `apply` fails with a 501 and the layer
cannot complete locally at all. The app layer depends on a real cluster. Under the original
sequencing, the AI platform layer would be gated on something that is deliberately unachievable
until a real AWS account is in play.

**New order, decided 2026-07-30:**

1. **AI platform Tier 1** (this ADR) — LiteLLM gateway, Langfuse, CI evals.
2. **EKS portability** — restructure `k8s/` into Kustomize `base/` + `overlays/{k3d,eks}/` so the
   k3d-specific values (pod/Service CIDRs, `storageClassName`, `ingressClassName`, image tags)
   become per-environment patches instead of hardcoded literals.
3. **App correctness** — real Elo + Poisson, consumer idempotency, dead-letter queue, backfill.

This is safe because **Tier 1 is entirely local k3d work and depends on no applied AWS resource.**
Every component is an ordinary cluster workload; the only external dependency is
`api.anthropic.com` over the internet, which needs no AWS anything. The IRSA and Secrets-Manager
story for the gateway credential is designed now and applied when the cluster layer is applied for
real — same treatment every other workload already gets.

**Consequence for how Tier 1 is built:** anything added in Tier 1 will be split into Kustomize
overlays in step 2. Avoid introducing new hardcoded k3d-specific values; where one is unavoidable,
comment it so step 2 can find it. The two CIDRs in `k8s/50-networkpolicies.yaml` are already
flagged this way and are the model to follow.
