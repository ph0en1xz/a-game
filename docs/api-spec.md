# A-Game — API Specification (v3)

> The public HTTP contract A-Game exposes. **API-only SaaS — no frontend in v1** (ADR 0003;
> Next.js browser client is Phase 2). **One endpoint** — decided 2026-08-10, amending ADR
> 0005's four-endpoint set (see that ADR's Amendments).

**Status:** Draft v3 · **Last updated:** 2026-08-10 · supersedes v2 (endpoint set reduced to
one; the per-fixture model/context/betting payload is dropped from the response — the
commentary prose carries the key numbers)

---

## 1. Shape of the product

A JSON HTTP API with a single client entrypoint, read-only `GET`, authenticated:

| Endpoint | Returns |
|---|---|
| `GET /v1/leagues/{league}/teams/{team}/suggestions` | The AI-written suggestion for that team's next fixture in that league |

The v2 endpoints (`/v1/leagues`, `/v1/leagues/{league}/fixtures`,
`/v1/leagues/{league}/suggestions`) are **deferred**, not deleted — see §8.

**No pending states.** Predictions are **precomputed** by the worker→brain pipeline
(daily, and only when upstream data actually changed — ADR 0005, cadence amended by ADR
0007). Every request is an immediate read; there is no `REQUEST_PENDING`, no polling, no
"come back later." A cache/database miss is a `404`, never a trigger for generation —
generation-on-request is the cost-DoS surface ADR 0005 removed.

## 2. Authentication

Static API keys (ADR 0005). Every request sends:

```
Authorization: Bearer <api-key>
```

- Keys are issued out-of-band, stored (hashed) in Postgres, and carry **per-key rate limits**.
- Missing/invalid/revoked key → `401`. Rate limit exceeded → `429` with `Retry-After`.
- There is **no** `/auth` endpoint — nothing to exchange; the key is the credential.

## 3. Identifiers

**`{league}`** — the football-data competition code, case-insensitive (`PL`, `pl`). Human
slugs (`premier-league`, `epl`) are **not** accepted: nothing upstream supplies them, so they
would be an alias set to invent and maintain for no evidenced need. Launch set = the
free-tier competitions, Premier League first (`input-spec.md` §10).

**`{team}`** — team slug or name, fuzzy-matched **within the given league**
(`brighton`, `brighton-hove-albion`). Ambiguous or no match → `404`.

There are no query parameters. The endpoint resolves to **one** fixture — the team's next
scheduled match in that league — so there is no window, market, or matchday to filter.

## 4. The read path

The server resolves team + league to the next upcoming fixture (Postgres), then serves the
suggestion **Redis-first**:

- Cache key `prediction:{match_id}`, holding the commentary only — never the raw engine
  metrics.
- **TTL expires at kickoff.** A suggestion for a match already underway is worthless, and
  kickoff-relative expiry removes stale entries without a sweeper.
- Miss → read `commentary` from Postgres, backfill Redis, return. Nothing in Postgres
  either → `404` (the pipeline hasn't processed that fixture yet).

The brain warms the cache when it writes the commentary, so a miss is the exception.

## 5. Freshness

- Suggestions are recomputed after an ingestion run **that changed data** (ADR 0007) —
  ingestion runs daily at 06:00 UTC, but a run that finds nothing new triggers no
  recomputation. Responses carry `generated_at` = when the pipeline produced the suggestion,
  so an unchanged `generated_at` means the inputs did not move.
- Data may therefore be up to ~24h stale — acceptable for weekly fixtures; the cadence is a
  pipeline setting, not an API contract.

## 6. The endpoint

```
GET /v1/leagues/{league}/teams/{team}/suggestions
```

The "should I back Brighton this weekend?" call — and the only call.

**Example:** `GET /v1/leagues/PL/teams/brighton/suggestions`

```json
{
  "league": "PL",
  "team": "Brighton & Hove Albion",
  "fixture": {
    "id": 538107,
    "home": "Manchester City FC",
    "away": "Brighton & Hove Albion FC",
    "utcDate": "2026-08-15T14:00:00Z",
    "matchday": 1,
    "status": "SCHEDULED"
  },
  "suggestion": {
    "preview": "AI-written 2-4 sentence narrative, quoting the model's own numbers.",
    "suggested_bet": "Home win",
    "suggested_bet_reason": "One sentence citing the model percentage and the baseline it departs from.",
    "source_model": "anthropic/claude-haiku-4-5-20251001"
  },
  "generated_at": "2026-08-10T06:04:12Z"
}
```

**The raw model numbers are not in the response** (decided 2026-08-10). The prose quotes the
probabilities that matter; the engine's full output (1X2, lambdas, Elo, score matrix) stays
in Postgres for calibration and is not part of the client contract. If a numbers block is
ever wanted, it is an additive change.

**`suggested_bet` is a divergence signal, not a tip** (decided 2026-08-10): the market where
the model departs most from the league baseline, **in either direction**. "Home win" can be
suggested because the model rates it *below* baseline. No odds are compared and no value
claim is made.

## 7. Errors

Standard HTTP status + a JSON body:

```json
{ "error": { "code": "team_not_found", "message": "No team matching 'foo' in PL." } }
```

| Status | `code` | When |
|---|---|---|
| `401` | `unauthorized` | Missing, invalid, or revoked API key |
| `404` | `league_not_found` | Unknown league code |
| `404` | `team_not_found` | No team matching the slug in that league |
| `404` | `no_upcoming_fixture` | Team exists but has no scheduled match ahead |
| `404` | `suggestion_not_ready` | Fixture exists but the pipeline hasn't produced a suggestion yet |
| `429` | `rate_limited` | Per-key rate limit exceeded (`Retry-After` header set) |
| `503` | `upstream_unavailable` | Store unavailable |

Four distinct `404` codes deliberately — collapsing them into one message makes "bad client
input" and "pipeline gap" indistinguishable in logs.

## 8. Deferred (tracked, not in the v3 contract)

- **The v2 endpoints** — leagues discovery, league fixtures, league-wide weekly suggestions.
  Cut 2026-08-10 to a single entrypoint; restore if a consumer materialises.
- **Raw model numbers in the payload** — additive if wanted (§6).
- **Odds dependency** — value bets need a paid odds feed (`input-spec.md` §6).
- **Key management ops** — issuance, rotation, revocation process (out-of-band for now).
- **Caching headers** — `ETag` / `Cache-Control` keyed on `generated_at`.
- **Phase 2** — Next.js browser client + WebSocket push (FastAPI native WS); live in-play
  data is a separate build (`input-spec.md` §9).
