# ADR 0006 — CI/CD: GitHub Actions now, GitOps (Argo CD) on EKS later

- **Status:** Accepted — **amended 2026-08-19** (see §Amendments). The title's "on EKS later" no
  longer holds: Argo CD now lands on k3d, ahead of EKS.
- **Date:** 2026-07-10
- **Deciders:** Mario (Nexoro Tech)
- **Related:** ADR 0008 (§Amendments 2026-08-19 — Argo Workflows withdrawn; Argo CD is the Argo
  product being adopted), ADR 0002 (EKS-for-learning), ADR 0009 (local validation split)

## Context

A-Game is built local-first on k3d — an ephemeral cluster, stopped when idle — with EKS as the prod learning target (ADR 0002). We needed to decide how code and manifests reach the cluster, and whether to adopt GitOps. GitHub Actions and GitOps are complementary, not alternatives: Actions is push-based CI/CD; GitOps is a pull-based in-cluster reconciler.

## Decision

- **CI/CD platform: GitHub Actions.** Free, matches the stack's documented tooling, strong portfolio signal. Scope, once app code exists: lint (ruff) + mypy + pytest + Docker image build; `terraform plan` on PRs and a gated `apply`, authenticated to AWS via **OIDC** (no static keys — reinforces the same no-static-credentials posture as IRSA).
- **Local deploys: `kubectl apply`.** For the ephemeral k3d cluster, direct apply is the right tool for learning the objects. No in-cluster reconciler locally.
- ~~**GitOps (Argo CD or Flux): deferred to the EKS/prod phase.**~~ **REVERSED 2026-08-19 — see §Amendments.** Original reasoning: GitOps needs a *persistent* cluster for its controller to reconcile against, which k3d is not. On EKS, Argo CD becomes the idiomatic deploy path and a portfolio artifact. Treated like IRSA/OIDC (ADR-0005 build phasing): design/write when useful, apply on the real EKS session.

## Consequences

- Nothing to wire until there is app code and a container image; CI is the first piece to land.
- GitOps work stays design-only until the local app is complete and tested, consistent with the Build phasing in the app `CLAUDE.md`.
- The Terraform pipeline uses GitHub OIDC → AWS, keeping the no-static-keys direction end to end.

## Amendments

### 2026-08-19 — GitOps moves ahead of EKS: Argo CD lands on k3d

**What changed.** The original decision deferred GitOps to the EKS/prod phase. The execution order
Marios set on 2026-08-18 and twice revised on 2026-08-19 puts **Argo CD first and EKS last**:

1. **Argo CD (GitOps)** — on k3d
2. Observability, all three stages (ADR 0011)
3. pgvector RAG
4. EKS

Argo CD moved ahead of observability in the second revision, so that every later item is deployed
*through* GitOps from the start rather than hand-applied and migrated afterwards.

That is a straight reversal of this ADR's third decision bullet, recorded here rather than left to
drift. It is also the resolution of the "Argo CD or Argo Workflows?" question: **Argo CD only.**
Argo Workflows was withdrawn the same day — see ADR 0008 §Amendments (2026-08-19).

**Why the original reasoning no longer blocks it.** "GitOps needs a persistent cluster" was
overstated. Argo CD's reconcile loop needs a cluster that is *running*, not one that is
*permanent*; on a k3d cluster that is started for a work session and stopped when idle, Argo CD
resyncs from Git on startup — which exercises exactly the drift-detection path worth learning.
What genuinely does not survive a k3d teardown is in-cluster state, and Argo CD's desired state
lives in Git by construction. The `Application` definitions are the artifact, and they are
cluster-independent.

**Why doing it first is better, not merely acceptable.**

- It avoids learning Argo CD and EKS simultaneously — two unfamiliar failure surfaces at once is
  the harder path, and when something breaks it is ambiguous which layer caused it.
- Everything after it (pgvector RAG, then EKS) gets deployed *through* GitOps, so the practice
  repeats instead of being retrofitted once at the end.
- On arrival at EKS, the `Application` manifests already exist and the work is re-pointing Argo CD
  at a new cluster — a narrower, better-understood task than a cold GitOps bootstrap on
  unfamiliar infrastructure.

**Costs, stated plainly.** Argo CD is a standing workload on an already-loaded laptop, alongside
ClickHouse (~2GB, ADR 0008 2026-07-30) and the incoming Prometheus/Grafana stack (ADR 0011). If
memory forces a choice, Argo CD yields before observability does. The Argo CD bootstrap is also
done twice in effect — once on k3d, once re-pointed at EKS — though the second pass is
configuration, not redesign.

**Unchanged by this amendment.** GitHub Actions remains the CI/CD platform with the same scope and
OIDC-to-AWS posture. `kubectl apply` remains correct for k3d **until** Argo CD is in place; after
that, `k8s/` is reconciled from Git and manual applies become drift.
