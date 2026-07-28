# A-Game — AI platform layer

Current as of **2026-07-22**. Design reference for the AI-infra scope expansion decided in
**ADR 0008**. This describes the platform *around* the models — not the models themselves
(Elo/Poisson lives in the engine; the LLM prompt lives in `docs/prompts/`).

Sequencing: this layer lands **after** the Terraform infra track (network → cluster → app).
Tier 1 first, then Tier 2; Tier 3 stays a design.

## Why this layer exists

A-Game already *uses* AI — the calc service calls Claude Haiku to write previews. That is an
application feature. AI-infra is the operational platform that makes running LLM workloads
observable, controllable, evaluable, and orchestrated. This layer adds that platform without
changing what gets predicted.

## Components

| Tier | Component | Runs as | Cost | Job |
|---|---|---|---|---|
| 1 | **LiteLLM gateway** | Deployment + Service | ~free | One controlled hop for every LLM call: rate limits, budget caps, caching, retries, fallback. Sole holder of the Anthropic credential; sole egress to `api.anthropic.com`. |
| 1 | **Langfuse** | Deployment + its own Postgres | ~free | Per-request tracing: tokens, cost, latency, prompt, response, model version. |
| 1 | **Eval harness** | GitHub Actions job | free | Structured-output validation + factuality vs. real stats + regression on a fixed fixture set; blocks merge on regression. |
| 2 | **pgvector RAG** | `vector` extension on existing Postgres | free | Retrieve similar historical matches to ground each preview. No new datastore. |
| 2 | **Argo Workflows** | Controller + CRDs | ~free | Retries, backfills, run UI for the daily pipeline. **Open fork (see below):** either replaces just the ingestion scheduler (calc stays event-driven) or models the whole pipeline as a DAG. Never triggers calc on a timer. |
| 3 | **vLLM / KServe** | GPU node group | $$ — **doc-only** | Self-hosted inference. Designed, provable briefly, never left running. |

## Request flow (with the platform in place)

```
scheduler (daily 06:00 UTC — CronJob today; Argo Tier 2)
        │  runs
        ▼
     ingest ──(change-gated "data ready" event)──► RabbitMQ ──► calc/predict ──► commentary
     (ADR 0007)                                                                       │
                                                    (Tier 2) retrieve similar ◄── pgvector (in Postgres)
                                                    matches to ground it              │
                                                                     calls cluster-internal
                                                                                      ▼
                                                            LiteLLM gateway ──────► api.anthropic.com:443
                                                                    │  emits trace     (only egress hop)
                                                                    ▼
                                                                Langfuse ──► its own Postgres
                                              store predictions + previews │
                                                                           ▼
                                              Postgres / Redis ──► FastAPI GET endpoints (unchanged)

CI (GitHub Actions): eval harness gates every merge on LLM-output regression.
```

**Cadence, stated plainly:** the pipeline is **daily and event-driven** (ADR 0007), not
timed-per-service. A daily tick runs ingestion; ingestion publishes a "data ready" event
**only when the upsert changed something**; the calc service is a long-running RabbitMQ
consumer that reacts. **Nothing runs calc "every N hours."** Argo (Tier 2) changes only *how
the run is orchestrated* — see the fork below — never how often calc computes.

Key change vs. ADR 0005: `commentary.py` no longer calls Anthropic directly. It calls the
**gateway's cluster-internal Service address**. The gateway holds the credential and owns the
outbound call.

## Orchestration: the Argo fork (Tier 2 — decide when we get there)

Argo adds retries, backfills, and a run UI. How it enters is an **open decision**, because the
pipeline is already event-driven and the two models don't stack:

- **(A) Scheduler only.** Argo replaces just the ingestion CronJob — the one daily tick.
  Downstream stays event-driven: ingestion still publishes the change-gated RabbitMQ event and
  calc still reacts. *Smallest change; keeps the messaging learning surface; RabbitMQ keeps its
  trigger role.*
- **(B) Full DAG.** Argo runs ingest → calc → store as explicit sequential steps. RabbitMQ
  loses its *trigger* role for this flow (stays only as the standalone messaging learning
  target). *Biggest orchestration signal; removes the event-driven decoupling.*

Not both — a DAG that also fires a RabbitMQ trigger for the same flow is redundant. Defaulting
toward (A) unless the portfolio value of a full DAG wins out. Either way, calc is never run on
a timer.

## Where it lands in the k8s / network topology

- **All Tier 1/2 workloads run in the private subnets** — same as the api and calc pods. None
  are internet-facing; none get a public ALB.
- **Egress collapses to one path.** Before: any pod calling Claude needed egress to
  `api.anthropic.com:443`. After: **only the LiteLLM gateway pod** does. The default-deny
  egress NetworkPolicy (queued, stage 2) then allows external `:443` from the gateway pod
  only, plus kube-dns `:53` cluster-wide. Everything else talks pod-to-pod on cluster-internal
  Services.
- **Credentials via IRSA.** The Anthropic key lives in a Secret consumed only by the gateway;
  the gateway's ServiceAccount is the only one scoped to read it. No other workload can.
- **Langfuse and its Postgres** are cluster-internal only (ClusterIP), reachable by the
  gateway for trace writes and by the operator (via port-forward) for the UI — never exposed.

This is the payoff for the networking focus: the platform is designed so the *blast radius of
the LLM credential and the LLM egress path is a single pod*, and the NetworkPolicy /  IRSA
scope reads straight off the topology.

## Cost posture

- **Tier 1 + 2:** ordinary CPU cluster workloads — $0 on LocalStack / local k3s, negligible on
  a running EKS beyond what the cluster already costs. LiteLLM caching *reduces* Anthropic
  spend.
- **Tier 3:** GPU node group is the only materially expensive piece — kept document-only and
  proven briefly, never standing.

## Build order

1. **LiteLLM gateway** — deploy, point `commentary.py` at it, move the Anthropic Secret behind
   it. (Do this first; it also tightens the egress story for the NetworkPolicy work.)
2. **Langfuse** — deploy + wire the gateway's tracing.
3. **Eval harness** — add the CI job + fixture set.
4. **pgvector RAG** — enable extension, add retrieval to `commentary.py`.
5. **Argo Workflows** — settle the fork first (defaulting to (A) scheduler-only), then
   introduce Argo accordingly.
6. **vLLM/KServe** — design ADR + Terraform GPU nodegroup; prove briefly; leave off.

## Impact on the existing k8s design & services

None of this is built yet — this is the change map from today's setup. Nothing here changes
what gets predicted; it changes where the LLM call goes and what observes it.

### Kubernetes design

- **Add workloads:** LiteLLM gateway (Deployment + ClusterIP Service + Secret + its own
  ServiceAccount), Langfuse (Deployment + its own Postgres, all ClusterIP), Argo Workflows
  (controller + CRDs + RBAC). *(Tier 3 GPU serving is designed, not deployed.)*
- **Move the Anthropic Secret.** Today the **calc** pod holds it. It moves to the **gateway's**
  Secret only, scoped via IRSA to the gateway's ServiceAccount — calc loses the credential
  entirely.
- **Egress NetworkPolicy (the queued stage-2 work) gets simpler and stricter:** default-deny
  egress; allow external `:443` **from the gateway pod only**, plus kube-dns `:53`. Everything
  else is pod-to-pod (calc→gateway, gateway→langfuse, calc→pgvector).
- **Ingestion CronJob → Argo** (Tier 2). Per the fork above: option (A) Argo replaces the
  CronJob as the daily scheduler and RabbitMQ keeps triggering calc; option (B) Argo runs the
  whole pipeline as a DAG and RabbitMQ drops its trigger role for this flow. Calc is not run on
  a timer in either case.
- **Diagrams to refresh when built:** `k8s-deployment-view.svg`, `cluster-runtime-view.svg` —
  add the new workloads.

### Python services

- **`commentary.py` (biggest change):** point the Anthropic client's `base_url` at the
  gateway's cluster-internal Service (e.g. `http://litellm-gateway.<ns>.svc:4000`) instead of
  `api.anthropic.com`. Remove `ANTHROPIC_API_KEY` from the calc env; add `LITELLM_BASE_URL`
  (+ a gateway virtual key).
- **Structured output:** `commentary.py` should return a validated JSON shape (not free text)
  so evals can assert on it — a prompt + response-parsing change.
- **pgvector retrieval (Tier 2):** add an embed → similarity-query step in `commentary.py`; a
  `vector` column/table + migration; one new dependency.
- **Eval harness:** new test module + fixture set, wired into the GitHub Actions workflow.
- **Argo (Tier 2):** each stage (ingest / calc / commentary) must be independently invokable
  as a DAG step — minor entrypoint tidy-up if they aren't already.

## Change list — what to change from the current setup

Concrete, current-state → target, in build order:

| # | Area | Change from today |
|---|---|---|
| 1 | k8s | **Add** LiteLLM gateway Deployment + ClusterIP Service + Secret + ServiceAccount (IRSA-scoped to the Anthropic Secret) |
| 2 | Secret | **Move** the Anthropic key: remove from the calc pod's env/Secret → attach to the gateway's Secret only |
| 3 | Python | **`commentary.py`:** swap the client `base_url` to the gateway Service; drop `ANTHROPIC_API_KEY`, add `LITELLM_BASE_URL` + virtual key |
| 4 | Python | **`commentary.py`:** return validated JSON (structured output) instead of free text |
| 5 | k8s | **Add** Langfuse Deployment + its own Postgres (ClusterIP); wire the gateway to emit traces to it |
| 6 | CI | **Add** an eval-harness job to the GitHub Actions workflow (structured-output + factuality + fixture regression); block merge on regression |
| 7 | NetworkPolicy | **Tighten** stage-2 egress: default-deny; external `:443` from the gateway pod only + kube-dns `:53`; pod-to-pod for the rest |
| 8 | Postgres/Python | **Enable** `vector` extension; add a vectors table/column + migration; add embed→similarity retrieval to `commentary.py` (Tier 2) |
| 9 | k8s/Python | **Introduce** Argo (Tier 2) — pick the fork first: (A) replace only the ingestion CronJob scheduler, calc stays a RabbitMQ consumer; or (B) full DAG, RabbitMQ drops its trigger role. Make each stage independently invokable only if (B) |
| 10 | Diagrams | **Refresh** `k8s-deployment-view.svg` + `cluster-runtime-view.svg` with the new workloads |
| — | Terraform | **Prereq:** the app-layer IaC gains the gateway ServiceAccount IRSA role + the Anthropic Secret wiring (lands with the app layer, after network → cluster) |

## Related documents

- [`../adr/0008-ai-platform-layer.md`](../adr/0008-ai-platform-layer.md) — the scope decision
- [`../adr/0005-v1-service-architecture.md`](../adr/0005-v1-service-architecture.md) — the pipeline this wraps
- [`network-topology.html`](network-topology.html) — the network layer these workloads sit in
- [`ai-platform-topology.html`](ai-platform-topology.html) — this layer, drawn
- [`../prompts/`](../prompts/) — the LLM prompt itself (out of scope here)
