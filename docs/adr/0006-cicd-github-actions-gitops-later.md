# ADR 0006 — CI/CD: GitHub Actions now, GitOps (Argo CD) on EKS later

- **Status:** Accepted
- **Date:** 2026-07-10
- **Deciders:** Mario (Nexoro Tech)

## Context

A-Game is built local-first on k3d — an ephemeral cluster, stopped when idle — with EKS as the prod learning target (ADR 0002). We needed to decide how code and manifests reach the cluster, and whether to adopt GitOps. GitHub Actions and GitOps are complementary, not alternatives: Actions is push-based CI/CD; GitOps is a pull-based in-cluster reconciler.

## Decision

- **CI/CD platform: GitHub Actions.** Free, matches the stack's documented tooling, strong portfolio signal. Scope, once app code exists: lint (ruff) + mypy + pytest + Docker image build; `terraform plan` on PRs and a gated `apply`, authenticated to AWS via **OIDC** (no static keys — reinforces the same no-static-credentials posture as IRSA).
- **Local deploys: `kubectl apply`.** For the ephemeral k3d cluster, direct apply is the right tool for learning the objects. No in-cluster reconciler locally.
- **GitOps (Argo CD or Flux): deferred to the EKS/prod phase.** GitOps needs a *persistent* cluster for its controller to reconcile against, which k3d is not. On EKS, Argo CD becomes the idiomatic deploy path and a portfolio artifact. Treated like IRSA/OIDC (ADR-0005 build phasing): design/write when useful, apply on the real EKS session.

## Consequences

- Nothing to wire until there is app code and a container image; CI is the first piece to land.
- GitOps work stays design-only until the local app is complete and tested, consistent with the Build phasing in the app `CLAUDE.md`.
- The Terraform pipeline uses GitHub OIDC → AWS, keeping the no-static-keys direction end to end.
