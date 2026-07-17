# A-Game — API Specification (v2)

> The public HTTP contract A-Game exposes. **API-only SaaS — no frontend in v1** (ADR 0003;
> Next.js browser client is Phase 2). Endpoint set and auth model per **ADR 0005**.
> Every prediction object is the engine's per-fixture output contract from
> `docs/input-spec.md` §8, wrapped for delivery.

**Status:** Draft v2 · **Last updated:** 2026-07-09 · supersedes v1 (endpoint set + auth
revised by ADR 0005; the pending/polling semantics of the draft design were dropped)

---

## 1. Shape of the product

A JSON HTTP API. Four endpoints, all read-only `GET`, all authenticated:

| # | Endpoint | Returns |
|---|---|---|
| 1 | `GET /v1/leagues` | Supported leagues (discovery) |
| 2 | `GET /v1/leagues/{league}/fixtures` | Upcoming fixtures this week (schedule only) |
| 3 | `GET /v1/leagues/{league}/suggestions` | Bet suggestions for every upcoming fixture in the league this week |
| 4 | `GET /v1/leagues/{league}/teams/{team}/suggestions` | Bet suggestions for one team's upcoming fixture(s) |

All responses are `application/json`, safe/idempotent, and cacheable.

**No pending states.** Predictions are **precomputed** by the ingestion→calculation pipeline
(daily, and only when upstream data actually changed — ADR 0005, cadence amended by ADR
0007). Every request is an immediate read; there is no `REQUEST_PENDING`, no polling, no
"come back later."

## 2. Authentication

Static API keys (ADR 0005). Every request sends:

```
Authorization: Bearer <api-key>
```

- Keys are issued out-of-band, stored (hashed) in Postgres, and carry **per-key rate limits**.
- Missing/invalid/revoked key → `401`. Rate limit exceeded → `429` with `Retry-After`.
- There is **no** `/auth` endpoint — nothing to exchange; the key is the credential.

## 3. Identifiers & shared conventions

**`{league}`** — the football-data competition code, case-insensitive (`PL`, `pl`). Human
slugs (`premier-league`, `epl`) are **not** accepted: nothing upstream supplies them, so they
would be an alias set to invent and maintain for no evidenced need. `GET /v1/leagues` is the
authoritative list of accepted values. Launch set = the free-tier competitions, Premier
League first (`input-spec.md` §10).

**`{team}`** — team slug or name, fuzzy-matched **within the given league**
(`brighton`, `brighton-hove-albion`). Ambiguous or no match → `404`.

**"Upcoming week"** — rolling window, default **`[now, now + 7 days]`**, so midweek rounds
are covered without assuming matchday == calendar week.

### Query parameters

| Param | Applies to | Default | Meaning |
|---|---|---|---|
| `from` / `to` | 2, 3, 4 | `now` / `now+7d` | ISO-8601 window override |
| `matchday` | 2, 3 | — | Select a specific league matchday instead of the date window |
| `markets` | 3, 4 | all | Comma filter: `1x2,over_under_2_5,btts` |
| `min_edge` | 3, 4 | `0` | Only selections with `edge >= min_edge` (value-bet filter) |

## 4. Freshness

- Predictions are recomputed after an ingestion run **that changed data** (ADR 0007) —
  ingestion runs daily at 06:00 UTC, but a run that finds nothing new triggers no
  recomputation. Responses carry `generated_at` = the timestamp of the pipeline run that
  produced them, so an unchanged `generated_at` means the inputs did not move.
- Data may therefore be up to ~24h stale — acceptable for weekly fixtures; the cadence is a
  pipeline setting, not an API contract.

## 5. The per-fixture object (shared payload)

Endpoints 3 & 4 return an array of these — the engine output contract (`input-spec.md` §8)
plus an AI-written `preview`. `betting` is `null` until an odds feed is wired
(`input-spec.md` §6); model probabilities are always present.

```json
{
  "fixture": {
    "id": "0",
    "home": "Manchester City",
    "away": "Brighton & Hove Albion",
    "utcDate": "2026-07-11T14:00:00Z",
    "matchday": 1,
    "competition": "Premier League",
    "venue": "Etihad Stadium",
    "status": "SCHEDULED"
  },
  "model": {
    "elo": { "home": 0, "away": 0, "hfa": 0 },
    "expected_goals": { "home": 1.75, "away": 1.0 },
    "result_prob": { "home_win": 0.62, "draw": 0.22, "away_win": 0.16 },
    "most_likely_scores": [ { "score": "2-1", "p": 0.11 } ],
    "over_2_5": 0.58,
    "btts": 0.50
  },
  "context": {
    "home_form_last5": "WWDWL", "away_form_last5": "LDWWL",
    "rest_days_home": 6, "rest_days_away": 4,
    "h2h": { "matches": 6, "home_wins": 4, "draws": 1, "away_wins": 1 }
  },
  "preview": "AI-written 2-3 sentence narrative for this fixture.",
  "betting": {
    "recommended": [
      { "market": "1x2", "selection": "Brighton win", "odds": 4.2, "model_prob": 0.16, "edge": -0.08, "ev": -0.33, "stake": "0u" },
      { "market": "over_under_2_5", "selection": "Over 2.5", "odds": 1.9, "model_prob": 0.58, "edge": 0.05, "ev": 0.10, "stake": "1u" }
    ],
    "all_evaluated": []
  }
}
```

`betting.recommended` is an **array** — one fixture can carry suggestions across several
markets ("Over 2.5 **and** Brighton to win"). With `min_edge` set, only positive-value
selections survive.

## 6. Endpoint 1 — Leagues

```
GET /v1/leagues
```

```json
{
  "count": 5,
  "leagues": [
    { "code": "PL", "name": "Premier League", "type": "LEAGUE" }
  ]
}
```

## 7. Endpoint 2 — Upcoming fixtures

```
GET /v1/leagues/{league}/fixtures
```

Schedule only — no predictions, no odds. The cheap "what's on this week" call.

```json
{
  "league": "Premier League",
  "window": { "from": "2026-07-09T00:00:00Z", "to": "2026-07-16T00:00:00Z" },
  "count": 10,
  "fixtures": [
    { "id": "0", "home": "Manchester City", "away": "Brighton & Hove Albion",
      "utcDate": "2026-07-11T14:00:00Z", "matchday": 1,
      "venue": "Etihad Stadium", "status": "SCHEDULED" }
  ]
}
```

## 8. Endpoint 3 — League weekly suggestions

```
GET /v1/leagues/{league}/suggestions
```

Suggestions for **every** fixture in the league inside the week window.

**Example:** `GET /v1/leagues/PL/suggestions?markets=1x2,over_under_2_5&min_edge=0.03`

```json
{
  "league": "Premier League",
  "window": { "from": "2026-07-09T00:00:00Z", "to": "2026-07-16T00:00:00Z" },
  "generated_at": "2026-07-09T09:00:00Z",
  "count": 10,
  "fixtures": [ /* array of §5 per-fixture objects */ ]
}
```

Empty `fixtures: []` with `count: 0` when nothing falls in the window — not a `404`.

## 9. Endpoint 4 — Team suggestions

```
GET /v1/leagues/{league}/teams/{team}/suggestions
```

Same object, filtered to one team's upcoming fixture(s) — typically one this week. The
"should I back Brighton this weekend, and is it a goals game?" call.

**Example:** `GET /v1/leagues/PL/teams/brighton/suggestions`

```json
{
  "league": "Premier League",
  "team": "Brighton & Hove Albion",
  "generated_at": "2026-07-09T09:00:00Z",
  "count": 1,
  "fixtures": [ /* §5 per-fixture object(s) for Brighton */ ]
}
```

`404` if the team isn't in that league; `count: 0` with an empty array if it has no fixture
in the window.

## 10. Errors

Standard HTTP status + a JSON body:

```json
{ "error": { "code": "league_not_found", "message": "Unknown league 'foo'." } }
```

| Status | `code` | When |
|---|---|---|
| `400` | `bad_request` | Malformed params (bad date, unknown market) |
| `401` | `unauthorized` | Missing, invalid, or revoked API key |
| `404` | `league_not_found` / `team_not_found` | Unknown league or team |
| `429` | `rate_limited` | Per-key rate limit exceeded (`Retry-After` header set) |
| `503` | `upstream_unavailable` | Pipeline has never run / store unavailable |

## 11. Follow-ups (tracked, not in the v2 contract)

- **Odds dependency** — `betting` is `null` until an odds feed is wired (`input-spec.md` §6).
- **Key management ops** — issuance, rotation, revocation process (out-of-band for now).
- **Caching headers** — `ETag` / `Cache-Control` keyed on `generated_at`.
- **Pagination** — only if a payload ever outgrows a league-week (~10 fixtures; unlikely).
- **Phase 2** — Next.js browser client + WebSocket push (FastAPI native WS); live in-play
  data is a separate build (`input-spec.md` §9).
