# ADR 0011 — Observability: metrics and logs for the cluster and the services

- **Status:** Proposed — 2026-08-18. Not yet decided.
- **Date:** 2026-08-18
- **Related:** ADR 0002 (EKS as the prod target), ADR 0008 (AI platform layer — Langfuse is
  LLM-only observability and does not cover this), ADR 0009 (local validation on k3s)
- **Deciders:** Mario (Nexoro Tech)

## Context

### What exists today

- **metrics-server** (bundled with k3s). Serves `kubectl top` and HPA. Point-in-time only:
  no storage, no history, no query language, no alerting.
- **Liveness and readiness probes** on every workload. Self-healing and load-balancer
  rotation — not observability.
- **Langfuse** (ADR 0008 Tier 1). Real retention in ClickHouse, but it sees only LLM calls
  passing through the LiteLLM gateway. It knows nothing about the queue, the database, the
  ingestion run, or the API.
- **`kubectl logs`.** Container stdout, retained only while the pod lives. One reschedule and
  the history is gone.

### What does not exist

No Prometheus, no Grafana, no Alertmanager, no ServiceMonitors. No log aggregation of any
kind. All four services call bare `logging.basicConfig(level=INFO)`, so logs are unstructured
plaintext carrying no request id, match id, or trace id — a single match cannot be followed
from worker to brain to api. No service exposes `/metrics`, so there are no application
signals at all: no queue depth, no prediction latency, no cache hit rate, no error rate.

The system can answer "is it up". It cannot answer "is it healthy" or "what happened an hour
ago".

### Two failures this already cost, both on 2026-08-18

1. **Resource undersizing found by hand.** ClickHouse was requesting 512Mi against 2146Mi of
   real usage and LiteLLM 512Mi against 1084Mi — eviction ratios of 4.2x and 2.1x. Both were
   discovered only because someone happened to run `kubectl top`. No alert existed to fire.
2. **A silently dropped trace.** LiteLLM's Langfuse callback fails soft by design: the
   completion returned 200, the preview was stored, and the only evidence of loss was rows
   that never arrived in ClickHouse. A panel showing traces-per-hour would have shown it in
   minutes. It took a day.

### Constraints

- **Node headroom is real but not generous.** The k3d node allocates 20 CPU and ~15.5Gi. Current
  requests total 5644Mi (35%), but *actual* usage is 9785Mi (61%) — the gap is the
  under-requesting described above. Roughly **5.7Gi of real headroom** remains.
- **ADR 0008 already flags cluster crowding** as its main negative: Tier 1 added five
  workloads, Tier 2 adds the Argo controller. Observability adds more.
- **Whatever is chosen must survive the move to EKS** (ADR 0002/0009). A Helm-installed
  stack does; anything hand-rolled against k3s specifics does not.

## Decision

**Proposed: stage it, metrics first.**

**Stage 1 — metrics.** `kube-prometheus-stack` (Prometheus, Grafana, Alertmanager,
node-exporter, kube-state-metrics) via Helm, with retention capped and scrape intervals
relaxed to suit a single-node cluster. Alerts for the failure that already bit: container
memory working set approaching its request, and pod restart rate.

**Stage 2 — application signals.** Structured JSON logging across all four services plus a
correlation id threaded from the RabbitMQ message through brain to the stored commentary, and
a `/metrics` endpoint on api and brain. **This is the cheapest item here and the one that
makes everything else useful** — Loki without structured logs is grep with extra steps.

**Stage 3 — logs.** Loki plus Grafana Alloy, behind the same Grafana. Deliberately last,
because its value depends on Stage 2 having happened.

### Options considered

| Option | What it adds | Rough memory request | Verdict |
|---|---|---|---|
| **A. Full stack now** (kube-prometheus-stack + Loki + Alloy) | Everything | ~3.5–4Gi | Fits, but consumes most remaining headroom in one move, before structured logs make the log half worth having |
| **B. Metrics only** (kube-prometheus-stack) | History, dashboards, alerting | ~2–2.5Gi | **Proposed.** Directly answers the question that bit twice |
| **C. Logs only** (Loki + Alloy) | Retention and search | ~1Gi | Premature: the logs it would retain are unstructured and uncorrelated |
| **D. Defer to EKS** (CloudWatch or AMP/AMG later) | Nothing now | 0 | Rejected: EKS is gated on a spend decision with no date, and the gap is causing real misses today |

## Consequences

- **Positive:** the resource-sizing class of bug becomes an alert instead of a lucky
  `kubectl top`. Trace throughput, queue depth, and error rate become visible over time.
- **Positive:** a Helm-installed kube-prometheus-stack moves to EKS unchanged, so the work is
  not thrown away at the cluster migration — unlike anything built around k3s specifics.
- **Positive (portfolio):** metrics, dashboards and alerting are the missing third of the
  operational surface ADR 0008 sets out to demonstrate. Langfuse covers LLM cost and latency;
  nothing covers the platform underneath it.
- **Negative (capacity):** Stage 1 takes roughly 2–2.5Gi of the ~5.7Gi headroom. Stage 3 takes
  another ~1Gi. Combined with Argo at Tier 2, the single node gets tight — and the requests
  already on the cluster understate real usage, so the headroom figure is optimistic.
- **Negative (surface):** four to six more workloads to deploy, secure, and write policies for.
  Every one needs its NetworkPolicy pair, and scrape traffic is a new cross-namespace flow.
- **Negative (effort):** Stage 2 touches all four services and the message envelope. It is the
  smallest infrastructure change here and the largest application change.
- **Neutral:** Langfuse stays as-is. It is the LLM trace store and is not replaced by this;
  the two answer different questions.

## Open questions

1. **Alert delivery.** Alertmanager to what — email, Slack, or nothing but the Grafana UI?
   Without a destination the alerting half is decorative.
2. **Retention.** How many days of metrics on a laptop cluster with local-path storage?
3. **Stage 2 correlation id.** Generated at ingestion and carried on the RabbitMQ message, or
   generated per prediction in brain? The first is more useful and touches more code.
4. **Ordering against Tier 2.** This competes with pgvector and Argo for the same headroom.
