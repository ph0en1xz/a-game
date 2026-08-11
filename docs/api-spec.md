# A-Game — API Specification (v4)

> The public HTTP contract A-Game exposes. **API-only SaaS — no frontend in v1** (ADR 0003;
> Next.js browser client is Phase 2). **One endpoint, keyed by match id** — decided
> 2026-08-11.

**Status:** Draft v4 · **Last updated:** 2026-08-11 · supersedes v3. v4 replaces the
team-and-league entrypoint with a direct **match id lookup**: the caller supplies
football-data's match id and gets that fixture's suggestion. League/team resolution, its
identifier rules, and the three 404 codes that belonged to it are gone.

---

## 1. Shape of the product

A JSON HTTP API with a single client entrypoint, read-only `GET`:

| Endpoint | Returns |
|---|---|
| `GET /{match_id}` | The AI-written suggestion for that fixture |

**The caller must know the match id.** No lookup by team, league or date — resolving a
human's "how do Brighton look this weekend?" into a match id is the client's problem, not the
API's. That is a deliberate narrowing of v3, which resolved league + team → next fixture
server-side.

The v2 endpoints (`/v1/leagues`, `/v1/leagues/{league}/fixtures`,
`/v1/leagues/{league}/suggestions`) and v3's team entrypoint are **deferred**, not deleted —
see §8.

**No pending states.** Predictions are **precomputed** by the worker→brain pipeline
(daily, and only when upstream data actually changed — ADR 0005, cadence amended by ADR
0007). Every request is an immediate read; there is no `REQUEST_PENDING`, no polling, no
"come back later." A cache/database miss is a `404`, never a trigger for generation —
generation-on-request is the cost-DoS surface ADR 0005 removed.

## 2. Authentication and rate limiting

> **Not built yet — deferred until after the EKS deployment** (decided 2026-08-11). Until then
> the API is unauthenticated and unthrottled: anything that can reach the Service can call it.
> Acceptable only because the sole deployment is local k3d with no public Ingress. This
> section is the contract to build against, not a description of what runs today.

Static API keys (ADR 0005). Every request sends:

```
Authorization: Bearer <api-key>
```

- **One key, in a Kubernetes Secret** — not a database table (decided 2026-08-11). The Secret
  holds the key's SHA-256 hash; the API hashes what arrives and compares with
  `hmac.compare_digest`, so the comparison leaks nothing through timing.
- Missing or invalid key → `401`. Rate limit exceeded → `429` with `Retry-After`.
- There is **no** `/auth` endpoint — nothing to exchange; the key is the credential.

A table was considered and rejected: it only earns its place with several keys to tell apart,
or revocation without a redeploy. With one caller, revocation *is* rotating the Secret. The
cost is that logs cannot distinguish callers. Revisit if a second consumer appears — that is
also when per-key limits become meaningful.

**Rate limiting.** A fixed window in Redis: **10 requests per 2 seconds**, one bucket per key.
`INCR` on `ratelimit:{key_hash}:{2s bucket}`, then `EXPIRE … 2 NX` in the same pipeline. The
`NX` matters — without it a crash between the two commands leaves a counter with no TTL,
permanently spending the quota.

- **Fixed, not sliding.** A caller can land up to 2× the limit across a bucket boundary. At
  this scale that is a non-event, and it costs one counter instead of two — but the limit is a
  ceiling on sustained rate, not an exact per-2-seconds guarantee.
- **The counter is charged after authentication**, never before. A rejected key consumes no
  quota, and an unauthenticated caller cannot fill Redis with counter keys.
- **Redis unreachable → fail open**, logged. The limiter guards against overuse, not attack,
  and the read path below already survives Redis being down; refusing traffic would convert a
  cache outage into an availability outage. Revisit if a limit ever becomes a billing
  boundary — then failing closed is the correct trade.
- `Retry-After` is the seconds remaining in the current window.

## 3. Identifiers

**`{match_id}`** — football-data's match id, an integer (`538107`). It is the primary key of
`a_game.match` and the id the whole pipeline already keys on, so nothing has to be resolved,
aliased or fuzzy-matched. A non-integer is a `422` from FastAPI's path validation before any
handler runs.

There are no query parameters. One id addresses exactly one fixture, so there is no window,
market or matchday to filter.

## 4. The read path

The id *is* the lookup key, so there is nothing to resolve first. The suggestion is served
**Redis-first**:

- Cache key `match_id:{match_id}`, holding the commentary only — never the raw engine
  metrics.
- **TTL is one ingestion cycle — a flat 24h from the write.** Ingestion runs daily at 06:00
  UTC (ADR 0007), so an entry outlives at most one cycle. Not computed to the next 06:00
  exactly: that needs a clock calculation in two services to buy a few minutes of precision
  nothing measures. Always positive, so there is no past-kickoff edge case to handle. Change
  the CronJob to a different cadence and this number is wrong.
- **Only a Postgres read backfills the cache**, never a cache hit — re-writing on every hit
  would keep refreshing the TTL and hold a fixture in Redis long after the pipeline stopped
  touching it. A `404` writes nothing, or the miss would be served for a day after the row
  arrived.
- Miss → read `commentary` from Postgres, backfill Redis, return. Nothing in Postgres
  either → `404` (the pipeline hasn't processed that fixture yet).

The brain warms the cache when it writes the commentary, so a miss is the exception.

**Value shape.** The preview prose, stored as a plain string — not a JSON object. It is
`commentary.prediction`, the same text the Postgres fallback returns, so both paths produce
an identical response and there is no shape to keep in sync across two services.

`suggested_bet`, `suggested_bet_reason` and `source_model` are written to Postgres by the
brain but are **not cached and not served** (§6). Adding them later means changing this value
to an object and the response alongside it.

The warm should happen **after** the Postgres transaction commits and be best-effort: a Redis
failure logged and swallowed rather than failing a job whose row is already written, and a
rolled-back transaction never leaving a cached entry behind.

## 5. Freshness

- Suggestions are recomputed after an ingestion run **that changed data** (ADR 0007) —
  ingestion runs daily at 06:00 UTC, but a run that finds nothing new triggers no
  recomputation.
- Data may therefore be up to ~24h stale — acceptable for weekly fixtures; the cadence is a
  pipeline setting, not an API contract.
- **The response carries no timestamp**, so a client cannot tell a re-generated suggestion
  from an unchanged one. `commentary.updated_at` holds it; serving it is the additive change
  in §8.

## 6. The endpoint

```
GET /{match_id}
```

The only call. Give it a fixture id, get that fixture's preview.

**Example:** `GET /538107`

```json
{
  "description": "AI-written 2-4 sentence narrative, quoting the model's own numbers.",
  "match_id": 538107
}
```

Two fields, deliberately. The prose already quotes the probabilities that matter, and echoing
`match_id` lets a client correlate a response without tracking the request.

**Nothing else is in the response** — not the fixture (teams, kickoff, matchday), not
`suggested_bet` or its reason, not `source_model`, not `generated_at`, and not the engine's
numbers (1X2, lambdas, Elo, score matrix). All of it is in Postgres. Every one of them is an
additive change if a consumer ever needs it; none has one today.

One note on a column this endpoint doesn't serve, since it survives in Postgres and would
resurface if the payload ever grows: **`suggested_bet` is a divergence signal, not a tip**
(decided 2026-08-10) — the market where the model departs most from the league baseline, in
either direction. No odds are compared and no value claim is made.

## 7. Errors

Standard HTTP status + a JSON body. FastAPI's own envelope, so the code and message sit under
`detail` rather than under an `error` key:

```json
{ "detail": { "code": "suggestion_not_ready", "message": "No suggestion for match 538107." } }
```

`401` and `429` are part of the contract but **not enforced until step 5 lands** (§2).

| Status | `code` | When |
|---|---|---|
| `401` | `unauthorized` | Missing or invalid API key |
| `404` | `suggestion_not_ready` | Neither Redis nor Postgres has a suggestion for that id — an unknown id and an unprocessed fixture are indistinguishable here |
| `422` | — | `{match_id}` is not an integer. FastAPI's own path validation, before any handler runs |
| `429` | `rate_limited` | Rate limit exceeded (`Retry-After` header set) |
| `503` | `upstream_unavailable` | Store unavailable |

**One `404` code, not four.** v3 had `league_not_found`, `team_not_found` and
`no_upcoming_fixture` because the server did the resolving. With the caller supplying the id
there is nothing left to resolve, so those three describe conditions that can no longer occur.

The cost is that a typo'd id and a fixture the pipeline hasn't reached both return
`suggestion_not_ready`. Distinguishing them would mean a second query against `match` purely
to produce a better error, which is not worth a round trip per miss.

## 8. Deferred (tracked, not in the v4 contract)

- **Authentication and rate limiting** — specified in §2, built after the EKS deployment
  (decided 2026-08-11). The contract keeps `401` and `429`; nothing enforces them yet.
- **Team/league resolution** — v3's `GET /v1/leagues/{league}/teams/{team}/suggestions`, which
  resolved a team's next fixture server-side. Cut 2026-08-11 in favour of the id lookup;
  restore if a consumer needs to ask by team rather than by id.
- **The v2 endpoints** — leagues discovery, league fixtures, league-wide weekly suggestions.
  Cut 2026-08-10; restore if a consumer materialises.
- **The rest of the payload** — fixture block, `suggested_bet` and its reason, `source_model`,
  `generated_at`, and the raw model numbers. All in Postgres, all additive (§6).
- **Odds dependency** — value bets need a paid odds feed (`input-spec.md` §6).
- **Key management ops** — issuance, rotation, revocation process (out-of-band for now).
- **Caching headers** — `ETag` / `Cache-Control` keyed on `generated_at`.
- **Phase 2** — Next.js browser client + WebSocket push (FastAPI native WS); live in-play
  data is a separate build (`input-spec.md` §9).
