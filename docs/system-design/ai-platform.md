# A-Game — AI platform layer

Current as of **2026-08-04**. Design reference for the AI-infra scope expansion decided in
**ADR 0008**. This describes the platform *around* the models — not the models themselves
(Elo/Poisson lives in the engine; the LLM prompt lives in `docs/prompts/`).

Sequencing: this layer lands **first**, ahead of the EKS-portability and app-correctness
tracks. ADR 0009 made the EKS cluster layer plan-only, so the original "after the Terraform
infra track" ordering was unsatisfiable — and Tier 1 depends on no applied AWS resource.
Tier 1 first, then Tier 2; Tier 3 stays a design.

> **Correction, 2026-07-30.** This document was written on 2026-07-22 against an assumed
> current state that was never built. Corrected throughout on that date to match ADR 0008's
> three amendments. The two claims that were most wrong: the LLM call did not exist (and still
> doesn't — `handlers.py` is a stub), and Langfuse v3 needs six components, not one Postgres.
>
> **Update, 2026-08-04.** The gateway now carries **two** providers with a fallback chain
> (ADR 0008 §Amendments, 2026-08-04). Sections below reflect that decision; the manifest change
> is **specified, not applied**, so nothing here should be read as observed behaviour. The
> gateway image bump lands first, as its own step.

## Why this layer exists

A-Game is *designed* to use AI — the brain service will ask the gateway for a preview, and the
gateway routes that to Claude Haiku (falling back to OpenAI). As of 2026-08-04 that call
**does not exist yet**: there is no `commentary.py`, and `anthropic` is declared in
`pyproject.toml` but never imported. AI-infra is the operational
platform that makes running LLM workloads observable, controllable, evaluable, and
orchestrated. This layer adds that platform *and* the first real call that exercises it,
without changing what gets predicted.

## Components

| Tier | Component | Runs as | Cost | Job |
|---|---|---|---|---|
| 1 | **LiteLLM gateway** | Deployment + Service | ~free | One controlled hop for every LLM call: rate limits, budget caps, caching, retries, fallback. Two routes — `claude-haiku` (Anthropic) primary, `gpt-4o-mini` (OpenAI) as its configured fallback. Sole holder of **both** provider credentials; the only workload with external egress besides the worker. |
| 1 | **Langfuse** | 4 new workloads (`langfuse-web`, `langfuse-worker`, ClickHouse, MinIO) + a database and a Redis index on the app's existing stores | ~free | Per-request tracing: tokens, cost, latency, prompt, response, model version. **v3 is six components, not one** — see ADR 0008 §Amendments (option C). |
| 1 | **Eval harness** | GitHub Actions job | free | Structured-output validation + factuality vs. real stats + regression on a fixed fixture set; blocks merge on regression. |
| 2 | **pgvector RAG** | `vector` extension on existing Postgres | free | Retrieve similar historical matches to ground each preview. No new datastore. |
| 2 | **Argo Workflows** | Controller + CRDs | ~free | Retries, backfills, run UI for the daily pipeline. **Open fork (see below):** either replaces just the worker scheduler (brain stays event-driven) or models the whole pipeline as a DAG. Never triggers brain on a timer. |
| 3 | **vLLM / KServe** | GPU node group | $$ — **doc-only** | Self-hosted inference. Designed, provable briefly, never left running. |

## Request flow (with the platform in place)

Boxes are pods. `commentary.py` is drawn *inside* brain because that is where it runs — it is a
module, not a service, and gets no Deployment of its own.

```
scheduler (daily 06:00 UTC — CronJob today; Argo Tier 2)
        │  runs
        ▼
     worker ──(change-gated "data ready" event)──► RabbitMQ ─────┐
     (ADR 0007)                                                  │ brain consumes & reacts
                                                                 ▼
                                                    ┌─────────────────────────────┐
                     (Tier 2) retrieve similar      │  brain / predict  (one pod) │
                     matches to ground it           │    Elo + Poisson            │
                        pgvector ◄──────────────────┤    commentary.py  (module)  │
                        (in Postgres)               └─────────────┬───────────────┘
                                                                 │ calls cluster-internal
                                                                 ▼
                                                        LiteLLM gateway
                                                                │       └──► api.anthropic.com:443  (claude-haiku, primary)
                                                                │       └──► api.openai.com:443     (gpt-4o-mini, fallback)
                                                                │  emits trace  (only egress hop for LLM traffic)
                                                                ▼
                                              langfuse-web + langfuse-worker
                                                                │
                                                                ▼
                                              ClickHouse · MinIO · (app Postgres + Redis)
        store predictions + previews
                    │
                    ▼
     Postgres / Redis ──► FastAPI GET endpoints (unchanged)

CI (GitHub Actions): eval harness gates every merge on LLM-output regression.
```

**Cadence, stated plainly:** the pipeline is **daily and event-driven** (ADR 0007), not
timed-per-service. A daily tick runs the worker; the worker publishes a "data ready" event
**only when the upsert changed something**; the brain is a long-running RabbitMQ
consumer that reacts. **Nothing runs brain "every N hours."** Argo (Tier 2) changes only *how
the run is orchestrated* — see the fork below — never how often brain computes.

Key change vs. ADR 0005: `commentary.py` never calls a provider directly. It calls the
**gateway's cluster-internal Service address** and asks for the model alias `claude-haiku`. The
gateway holds the credentials, picks the provider, and owns the outbound call — including the
fallback to `gpt-4o-mini` if the primary route fails, which the brain never sees. (ADR 0005
assumed a direct SDK call; because the module is being written for the first time now, this is
how it ships rather than something to migrate.)

## Orchestration: the Argo fork (Tier 2 — decide when we get there)

Argo adds retries, backfills, and a run UI. How it enters is an **open decision**, because the
pipeline is already event-driven and the two models don't stack:

- **(A) Scheduler only.** Argo replaces just the worker CronJob — the one daily tick.
  Downstream stays event-driven: worker still publishes the change-gated RabbitMQ event and
  brain still reacts. *Smallest change; keeps the messaging learning surface; RabbitMQ keeps its
  trigger role.*
- **(B) Full DAG.** Argo runs worker → brain → store as explicit sequential steps. RabbitMQ
  loses its *trigger* role for this flow (stays only as the standalone messaging learning
  target). *Biggest orchestration signal; removes the event-driven decoupling.*

Not both — a DAG that also fires a RabbitMQ trigger for the same flow is redundant. Defaulting
toward (A) unless the portfolio value of a full DAG wins out. Either way, brain is never run on
a timer.

## Where it lands in the k8s / network topology

- **All Tier 1/2 workloads run in the private subnets** — same as the api and brain pods. None
  are internet-facing; none get a public ALB.
- **Egress collapses to one path.** Before: any pod calling Claude needed egress to
  `api.anthropic.com:443`. After: **only the LiteLLM gateway pod** does. The default-deny
  egress NetworkPolicy — **applied 2026-07-30**, see runbook `2026-07-13-networkpolicies` —
  then allows external `:443` from the gateway pod only, plus kube-dns `:53` cluster-wide.
  Everything else talks pod-to-pod on cluster-internal Services. Today the worker
  holds the only `0.0.0.0/0:443` rule (football-data.org); the gateway becomes the second and
  last one. **That rule is `0.0.0.0/0`, not provider-scoped** — both model providers sit behind
  CDNs with rotating address ranges, so an `ipBlock` naming them is not maintainable. Adding a
  second provider therefore requires no NetworkPolicy change at all, which is the honest reading
  of "collapses to one path": one *pod* is the enforced boundary, one *destination* is not.
- **Credentials via IRSA.** Both provider keys — `anthropic-credentials` and
  `openai-credentials` — live in Secrets consumed only by the gateway; the gateway's
  ServiceAccount is the only one scoped to read them. No other workload can. (Locally these are
  hand-created Secrets — IRSA is the EKS half of the story.) The blast radius is still a single
  pod, but that pod now holds two providers' keys rather than one; see ADR 0008 §Amendments
  (2026-08-04) for why a gateway-per-provider was rejected.
- **Langfuse and its datastores** are cluster-internal only (ClusterIP), reachable by the
  gateway for trace writes and by the operator (via port-forward) for the UI — never exposed.

This is the payoff for the networking focus: the platform is designed so the *blast radius of
the LLM credential and the LLM egress path is a single pod*, and the NetworkPolicy /  IRSA
scope reads straight off the topology.

## Cost posture

- **Tier 1 + 2:** ordinary CPU cluster workloads — $0 on LocalStack / local k3s, negligible on
  a running EKS beyond what the cluster already costs. LiteLLM caching *reduces* provider spend.
- **Two providers, two bills (2026-08-04).** Budget caps are per route, so the cap set on the
  Haiku route does not bound the fallback. A primary outage that trips the fallback shifts spend
  to OpenAI silently unless `gpt-4o-mini` carries its own cap — set both, not one.
- **Tier 3:** GPU node group is the only materially expensive piece — kept document-only and
  proven briefly, never standing.

## Build order

1. **LiteLLM gateway** — create the provider Secrets, deploy the gateway, then write
   `commentary.py` pointed at it. (Do this first; it also tightens the egress story for the
   NetworkPolicy work.) As of 2026-08-04 this is a four-step sequence, each verified before the
   next: **bump the image** → **confirm the Haiku route still answers** → **add the OpenAI route**
   → **add `fallbacks` and prove the switch fires**. See ADR 0008 §Amendments (2026-08-04).
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

- **Add workloads (Tier 1 = five, all off-the-shelf images):** LiteLLM gateway (Deployment +
  ClusterIP Service + Secret + its own ServiceAccount), `langfuse-web` + `langfuse-worker`
  Deployments, ClickHouse and MinIO StatefulSets — all ClusterIP. Tier 2 adds Argo Workflows
  (controller + CRDs + RBAC). *(Tier 3 GPU serving is designed, not deployed.)*
- **Create the Anthropic Secret on the gateway.** Nothing holds it today — the brain pod never
  had it, because the LLM call was never built. It is created directly against the
  **gateway's** ServiceAccount, scoped via IRSA on EKS. brain never receives it.
- **Egress NetworkPolicy — the lockdown is already applied (2026-07-30).** What Tier 1 changes:
  brain gains egress to the gateway on `:4000` and gets **no** internet rule; the gateway gains
  the second and last `0.0.0.0/0:443` rule. Everything else stays pod-to-pod
  (brain→gateway, gateway→langfuse, brain→pgvector).
- **Worker CronJob → Argo** (Tier 2). Per the fork above: option (A) Argo replaces the
  CronJob as the daily scheduler and RabbitMQ keeps triggering brain; option (B) Argo runs the
  whole pipeline as a DAG and RabbitMQ drops its trigger role for this flow. Brain is not run on
  a timer in either case.
- **Diagrams to refresh when the gateway is actually deployed — all four carry a direct
  `brain → Claude API` edge that Tier 1 breaks:** `a-game-architecture.svg` (External layer),
  `flow-1-precompute-pipeline.svg` (step 8, "narrate preview per fixture"),
  `k8s-deployment-view.svg` ("calls Claude"), `cluster-runtime-view.svg` (the egress arrow and
  the Secrets panel). They are correct as of today and are deliberately **not** pre-emptively
  redrawn — documenting an unbuilt gateway is the same defect this document was just corrected
  for. Refresh them once, against a running gateway.

### Python code

**No new Python services.** This is worth stating flatly, because an earlier version of this
document listed the changes under a "Python services" heading and drew `commentary.py` as a
peer node in the topology diagram — both of which read as a new workload. It is a module
inside the existing brain service. Tier 1 adds **zero** Deployments that you write Python for:
the five new workloads are all off-the-shelf images you configure with YAML.

- **`commentary.py` (new file, ~50 lines):** builds the preview prompt from the match row and
  calls the gateway's cluster-internal Service (e.g. `http://litellm-gateway.<ns>.svc:4000`),
  never `api.anthropic.com`. Uses the **`openai` client library**, not `anthropic` — LiteLLM
  speaks the OpenAI wire format, so `anthropic` comes out of `pyproject.toml` and `openai`
  goes in. brain's env gets `LITELLM_BASE_URL` (+ a gateway virtual key) and never an
  `ANTHROPIC_API_KEY`. That the app code stops being Anthropic-specific is a feature of
  putting a gateway in front, not a side effect.
- **`handlers.py` (edit):** call `commentary.py` from the existing stub.
- **`config.py` (edit):** add the gateway URL setting alongside the rabbitmq/redis/postgres
  fields.
- **Structured output:** `commentary.py` returns a pydantic-validated JSON shape (not free
  text) so evals can assert on it.
- **pgvector retrieval (Tier 2):** add an embed → similarity-query step in `commentary.py`; a
  `vector` column/table + migration; one new dependency.
- **Eval harness:** new test module + fixture set, wired into the GitHub Actions workflow.
- **Argo (Tier 2):** only if the fork resolves to (B) full DAG — each stage must be
  independently invokable as a DAG step (a separate entrypoint into the *same* image, not a
  separate service). Minor entrypoint tidy-up; commentary stays part of brain either way.

## Change list — what to change from the current setup

Concrete, current-state → target, in build order:

| # | Area | Change from today |
|---|---|---|
| 1 | k8s | **Add** LiteLLM gateway Deployment + ClusterIP Service + Secrets + ServiceAccount (IRSA-scoped to the provider Secrets) |
| 2 | Secret | **Create** the Anthropic key as a Secret attached to the gateway only (nothing holds it today — the call was never built), and `openai-credentials` alongside it for the fallback route. Both arrive via `envFrom` on the gateway Deployment; `envFrom` takes a list, so the second is an added entry, not a replacement |
| 2b | k8s | **Bump** `ghcr.io/berriai/litellm` off `main-v1.61.1` — a year-old image holding every model credential in the project. Taken as its own step *before* the second route, so config-schema drift and a new `model_list` entry are not debugged together |
| 2c | k8s | **Add** the `gpt-4o-mini` route to the same `litellm-config` ConfigMap (one more `model_list` entry, not a second ConfigMap) plus `litellm_settings.fallbacks` mapping `claude-haiku → [gpt-4o-mini]`. LiteLLM parses `config.yaml` at startup, so editing the ConfigMap alone is a no-op — a `rollout restart` is required |
| 3 | Python | **`commentary.py` (new file, in the brain pod — not a new service):** `openai` client pointed at the gateway Service; `LITELLM_BASE_URL` + virtual key in brain's env, never `ANTHROPIC_API_KEY`. Swap `anthropic` → `openai` in `pyproject.toml` |
| 4 | Python | **`commentary.py`:** return pydantic-validated JSON (structured output), not free text |
| 5 | k8s | **Add** `langfuse-web` + `langfuse-worker` Deployments, ClickHouse + MinIO StatefulSets (all ClusterIP); give Langfuse a dedicated database on the app Postgres and a dedicated `REDIS_DB` index (option C); wire the gateway to emit traces to it |
| 6 | CI | **Add** an eval-harness job to the GitHub Actions workflow (structured-output + factuality + fixture regression); block merge on regression |
| 7 | NetworkPolicy | **Extend** the existing lockdown (applied 2026-07-30): brain gains egress to the gateway on `:4000` and no internet rule; new `allow-litellm-egress` (`0.0.0.0/0:443`, `except` pod + Service CIDRs) and `allow-litellm` ingress from brain on `:4000` |
| 8 | Postgres/Python | **Enable** `vector` extension; add a vectors table/column + migration; add embed→similarity retrieval to `commentary.py` (Tier 2) |
| 9 | k8s/Python | **Introduce** Argo (Tier 2) — pick the fork first: (A) replace only the worker CronJob scheduler, brain stays a RabbitMQ consumer; or (B) full DAG, RabbitMQ drops its trigger role. Make each stage independently invokable only if (B) |
| 10 | Diagrams | **Refresh** `k8s-deployment-view.svg` + `cluster-runtime-view.svg` with the new workloads |
| — | Terraform | **Not a prereq (revised 2026-07-30).** The app-layer IaC gains the gateway ServiceAccount IRSA role + Anthropic Secret wiring, but that is the EKS half and lands later — ADR 0009 keeps the cluster layer plan-only. Tier 1 runs entirely on k3d with a hand-created Secret |

## Related documents

- [`../adr/0008-ai-platform-layer.md`](../adr/0008-ai-platform-layer.md) — the scope decision
- [`../adr/0005-v1-service-architecture.md`](../adr/0005-v1-service-architecture.md) — the pipeline this wraps
- [`network-topology.html`](network-topology.html) — the network layer these workloads sit in
- [`ai-platform-topology.html`](ai-platform-topology.html) — this layer, drawn
- [`../prompts/`](../prompts/) — the LLM prompt itself (out of scope here)
