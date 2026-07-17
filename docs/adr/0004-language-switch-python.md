# ADR 0004 — Switch implementation language to Python

- **Status:** Accepted
- **Date:** 2026-07-09
- **Supersedes:** the language/runtime portion of ADR 0001 (Node.js + TypeScript). All other ADR 0001 stack decisions — Postgres-only, Claude AI layer, Terraform, Docker — still stand.
- **Deciders:** Mario (Nexoro Tech)

## Context

ADR 0001 chose Node.js + TypeScript. During the 2026-07-09 architecture review the owner
decided to build A-Game in Python instead. The timing makes the switch nearly free: the
application is still at zero code (Track B, infra-first), so nothing is thrown away.

A draft design had also floated uWebSockets.js for the WebSocket layer — a Node-only native
library, impossible on a Python stack. That conflict is moot: ADR 0005 defers WebSockets to
Phase 2 entirely, and when they arrive they'll use FastAPI's native WebSocket support.

## Decision

- **Python 3.12+** across all services.
- **API service:** FastAPI + pydantic. Pydantic models validate every external boundary
  (HTTP requests, football-data.org payloads, Claude responses) — the Python equivalent of
  the home base's Zod-at-boundaries convention.
- **Workers** (calculation/AI, ingestion): plain Python, sharing the same pydantic models.
- **Tooling:** `ruff` (lint + format), `mypy` (type checking), `pytest`; **`uv`** for
  dependency and environment management.
- **Claude integration:** official `anthropic` Python SDK.
- The ADR 0001 principle of **portable, framework-agnostic core modules** (data client,
  Elo/Poisson engine, AI layer) carries over unchanged — core logic must not import FastAPI.

## Consequences

- **Positive:** switch costs nothing now (zero code exists); Python is a natural fit for the
  statistical core (Elo/Poisson math, data transformation); one language across API and
  workers.
- **Negative:** TypeScript-specific conventions in project docs are retired; the Phase 2
  Next.js frontend will make the repo polyglot eventually (acceptable — it's a separate
  client with its own toolchain).
- **Neutral:** Docker/k8s/Terraform/LocalStack setup is language-agnostic and unaffected.
  `TECHSTACK.md` rewritten to match.
