# ADR 0003 — Product surface: API-only SaaS, three endpoints, no frontend

- **Status:** Accepted
- **Date:** 2026-07-09
- **Deciders:** Mario (Nexoro Tech)
- **Related:** `docs/api-spec.md` (the contract), `docs/input-spec.md` §8 (engine output object)

## Context

A-Game's engine (Elo + Poisson → probabilities, plus an AI preview/value-bet layer) produces a
per-fixture output object. That object needs a delivery surface. The question was whether v1
ships a web frontend or exposes the predictions purely as an HTTP API.

The owner decided v1 is **API-only**: no frontend, delivered as a SaaS. Consumers (or another
service) call the JSON API directly. A UI, if ever, is a separate later concern that would sit
on top of the same API.

## Decision

- **v1 is an API-only SaaS.** No frontend is built.
- Expose exactly **three read-only `GET` endpoints**:
  1. `GET /v1/leagues/{league}/suggestions` — bet suggestions for every upcoming fixture in the
     league this week.
  2. `GET /v1/leagues/{league}/teams/{team}/suggestions` — bet suggestions for one team's
     upcoming fixture(s) (e.g. "Brighton to win", "Over 2.5 goals").
  3. `GET /v1/leagues/{league}/fixtures` — the upcoming week's fixtures, schedule only.
- The suggestion endpoints return the existing engine output contract (`input-spec.md` §8)
  wrapped with an AI `preview`; the fixtures endpoint returns a lightweight schedule object.
- **"Upcoming week"** = a rolling `[now, now+7d]` window by default (overridable), so midweek
  rounds are covered without assuming matchday == calendar week.
- **`{league}` / `{team}`** accept human slugs or codes and are mapped/fuzzy-matched server-side.
  > **Superseded in part (2026-07-16):** `{league}` accepts the **football-data code only**
  > (`PL`). Human slugs (`premier-league`, `epl`) are not supplied upstream — honouring them
  > meant inventing and maintaining an alias set for no evidenced need, so they were dropped
  > rather than implemented. `{team}` fuzzy-matching by name stands unchanged. Contract:
  > `docs/api-spec.md` §3. Too small for its own ADR; recorded here.
- Full contract lives in `docs/api-spec.md`.

## Consequences

- **Positive:** smallest correct surface for a prediction product — the engine's value is the
  data, and an API delivers it without UI cost; clean separation lets a UI (or partner
  integrations) be added later against a stable contract; each endpoint maps directly to one
  engine capability.
- **Negative / deferred:** SaaS implies **authentication + rate limiting** (API keys,
  plan-based limits) which are **not** in the v1 contract and must be designed before any public
  exposure; value-bet fields depend on the (paid) odds feed and are `null` until it's wired.
- **Neutral:** stack and infra decisions (ADR 0001/0002) are unaffected — this defines the
  delivery contract, not the compute or deployment model.
```
