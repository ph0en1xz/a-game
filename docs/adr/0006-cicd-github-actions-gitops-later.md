# ADR 0006 — CI/CD: GitHub Actions now, GitOps (Argo CD) on EKS later

- **Status:** Accepted — **amended 2026-08-19 and 2026-08-24** (see §Amendments). The title's "on EKS later" no
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

### 2026-08-24 — the settings actually chosen, and Argo CD self-management

The 2026-08-19 amendment decided *that* Argo CD lands on k3d. This records *how* it was configured,
so the choices are recoverable without reading the manifests.

**Repository and application layout.** One repository, not a separate config repo — a second repo is
overhead for one person. Two `Application`s, both in project `default`:

| Application | `path` | Destination namespace | Manages |
|---|---|---|---|
| `a-game` | `k8s` | `a-game` | the platform and application workloads |
| `argocd` | `k8s/argocd` | `argocd` | Argo CD itself, its NetworkPolicies, and both Application manifests |

Because `k8s/argocd` contains `04-application-a-game.yaml`, the `argocd` Application manages the
`a-game` Application. That is the app-of-apps pattern, reached without a separate root manifest.

**Sync settings, and the order they were enabled.** Both Applications started with no `syncPolicy`
at all — manual sync, no prune, no self-heal. Auto-sync was enabled on `a-game` first
(`syncPolicy.automated: {}`), deliberately one change at a time: `automated` with empty braces turns
on auto-sync while leaving prune and self-heal off. Prune and self-heal remain off pending several
uneventful syncs. Neither Application carries `metadata.finalizers`, so
`kubectl delete application` does not cascade into deleting managed resources — right eventually,
wrong while learning.

**`ServerSideApply=true` is required, not stylistic.** Client-side apply writes the full manifest
into the `kubectl.kubernetes.io/last-applied-configuration` annotation, and the
`applicationsets.argoproj.io` CRD exceeds the 262,144-byte annotation limit. Without the sync
option the first sync of `install.yaml` fails with `metadata.annotations: Too long`. The same
applies to CI's dry-run step, which needs `--server-side`.

**`ignoreDifferences` on two Secrets.** `install.yaml` declares `argocd-secret` and
`argocd-notifications-secret` as empty shells — name, labels, `type: Opaque`, no `data`. The live
`argocd-secret` holds `admin.password`, `admin.passwordMtime` and `server.secretkey`, all generated
or set at runtime. Both Secrets' `/data` is therefore excluded from comparison; without it the
Application is permanently OutOfSync, and self-heal would reconcile login credentials toward an
empty declaration.

**Upstream `install.yaml` is vendored, not curled.** The pinned v3.5.1 manifest is committed at
`k8s/argocd/02-install-argocd.yaml`. Applying it from a URL left Argo CD's own seven workloads
invisible to Git — the one component outside its own supervision. Two consequences follow: the
directory now describes Argo CD completely, and resource requests/limits become an ordinary commit
rather than a `kubectl edit` that drifts.

**Resource requests and limits were added on top of upstream.** Upstream ships none, leaving all
seven pods BestEffort and first to be evicted next to ClickHouse at ~2.1Gi. Values were measured
from an idle cluster rather than guessed. CPU requests only, no CPU limits — throttling causes
latency and the request is what scheduling uses; memory carries both, because memory cannot be
throttled, only killed. The edits live inside the vendored file, so an Argo CD version bump means
re-vendoring and re-applying them.

**Egress NetworkPolicies are ours; ingress is upstream's.** `install.yaml` ships seven
ingress-only NetworkPolicies, one per component, and zero egress rules — deliberately, since
egress depends on the API server address, the git host and whether SSO is used. The seven egress
policies in `k8s/argocd/03-networkpolicies-argocd.yaml` are the local half.
