# ADR 0002 — Revise production execution model: EKS as the learning target

- **Status:** Accepted
- **Date:** 2026-07-08
- **Supersedes:** the production-execution-model decision in ADR 0001 (the tech-stack portion of ADR 0001 still stands)
- **Deciders:** Mario (Nexoro Tech)

## Context

ADR 0001 chose a serverless production model (Lambda container image + EventBridge Scheduler + API Gateway) primarily to minimize idle cost. Since then the owner clarified the actual priority for this project: gaining hands-on Kubernetes experience. A-Game is a learning vehicle first; cost-optimal production is secondary until the app exists.

Under a serverless production target, the local k3s cluster would be a throwaway artifact disconnected from how the app deploys — which undercuts the learning goal.

## Decision

- Treat **EKS as the working production target** for now, so the whole path (local k3s to prod EKS) is Kubernetes end to end and the local work carries forward.
- Structure the Terraform in the standard layered form: **network** (VPC, subnets, routing), then **cluster** (EKS control plane, node groups, IRSA), then **app** (workloads, application IAM, RDS, S3) — each with its own state key for blast-radius segmentation.
- **Defer the final production-deployment decision** until the app is built and ready. EKS vs serverless vs anything else is re-evaluated then. Building on EKS now is explicitly a learning choice and does not lock the eventual production platform.

## Consequences

- **Positive:** local k3s and prod EKS share one model, so local learning transfers directly; the layered Terraform matches the terraform-engineering skill natively; maximum Kubernetes exposure.
- **Negative:** an eventual switch to serverless at deploy time would discard some EKS-specific IaC; real EKS in production later costs materially more than serverless (not a concern now — everything is local and $0 on LocalStack).
- **Neutral:** ADR 0001 stack choices (Node/TS, Postgres, Terraform, Docker, Claude AI layer, LocalStack, $0 local-first) are unaffected; only its prod-execution portion is superseded.
