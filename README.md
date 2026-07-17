# A-Game

Football match predictions, served as an API. It ingests results from
[football-data.org](https://www.football-data.org), rates teams with Elo, turns those ratings into
scoreline probabilities with a Poisson model, and has Claude write the preview text that explains
what the numbers say.

The maths is deterministic and lives in code. The AI only phrases the output — it never invents a
statistic.

## Why this exists

This is a for-fun project, built mostly for demonstration purposes — a learning vehicle and a
portfolio piece, not a product. Nobody is meant to depend on it.

Honestly? To learn Kubernetes properly, and to have something real to point at.

A predictions API is a good excuse: it needs scheduled ingestion, a database that actually models
something, a message broker, a cache, precomputed reads, and infrastructure to run it all on. That
covers most of the interesting ground without being a toy. Design decisions are argued out in
[`docs/adr/`](docs/adr/) rather than made by reflex, which is half the point.

It runs on my laptop and I use it about once a week. It is not a product, and nothing here is
load-bearing for anyone but me.

## How it works

Three services, one pipeline:

**ingestion** (`a-game-worker`) — a CronJob that runs daily at 06:00 UTC. It fetches from
football-data, upserts the facts into Postgres, and publishes a "data ready" job *only if the upsert
actually changed a row*. A run that changes nothing publishes nothing and exits. That constraint is
the whole design ([ADR 0007](docs/adr/0007-ingestion-cadence-daily-change-gated.md)): fixtures move
in weekend and midweek clusters, so most runs have nothing to say, and firing the AI pipeline on
byte-identical inputs is just spending money to recompute yesterday.

**calc** (`a-game-brain`) — consumes that job, recomputes ratings and probabilities for upcoming
fixtures, has Claude narrate them, writes the results back to Postgres and warms Redis.

**api** (`a-game-api`) — four read-only `GET` endpoints that serve what calc already computed.
Nothing is calculated on request; there are no pending states and nothing to poll. Contract in
[`docs/api-spec.md`](docs/api-spec.md).

## Stack

Python 3.12 with FastAPI and pydantic, managed by `uv`. PostgreSQL is the only system of record —
raw payloads land in JSONB alongside typed columns, and predictions are kept permanently and tagged
with the model version, so accuracy can be measured after the fact instead of assumed. RabbitMQ
carries jobs between services. Redis is a read-through cache and nothing else; you could delete it
and the design would still hold.

Everything is containerized and runs on Kubernetes — k3s via k3d locally, EKS as the eventual
target. Infrastructure is Terraform, run against LocalStack so local development costs nothing.

The full breakdown, including what's decided and what isn't, is in
[`TECHSTACK.md`](TECHSTACK.md).

## Status

Early. The cluster runs, the schema is settled, and ingestion fetches competitions and seasons into
Postgres on a schedule. What isn't done:

- Match ingestion — the change gate and the fixture upsert
- The Elo/Poisson engine is written but not wired to anything
- Claude narration is written but not wired either
- The predictions half of the schema isn't modelled yet
- No CI, no tests worth the name
- No odds feed, so value bets are `null` (odds are behind a paywall)

## Running it locally

You'll need Docker, [k3d](https://k3d.io), `kubectl`, and [uv](https://docs.astral.sh/uv/).

```bash
# supporting services (LocalStack, Postgres)
docker compose up -d

# cluster
k3d cluster create a-game
kubectl apply -f k8s/
```

Secrets are created imperatively and never committed:

```bash
kubectl create secret generic sports-api-credentials -n a-game \
  --from-literal=SPORTS_API_KEY='<your football-data.org key>'
```

Apply the schema, in order — the files are numbered for foreign-key dependencies:

```bash
cd a-game-worker/postgres
for f in *.sql; do
  kubectl exec -i -n a-game a-game-postgres-0 -- \
    psql -U postgres -d a_game_db -v ON_ERROR_STOP=1 < "$f" || break
done
```

The worker won't fetch anything until you pick a league — competitions arrive disabled by default:

```sql
UPDATE a_game.competition SET enabled = true WHERE code = 'PL';
```

To run ingestion now rather than waiting for 06:00:

```bash
kubectl create job --from=cronjob/a-game-worker worker-manual-1 -n a-game
kubectl logs -n a-game job/worker-manual-1 -f
```

## Layout

```
a-game-api/        FastAPI read API
a-game-brain/      prediction engine + Claude narration
a-game-worker/     ingestion CronJob, plus the schema DDL in postgres/
k8s/               manifests, applied in numeric order
infrastructure/    Terraform, layered: network → cluster → app
docs/adr/          why things are the way they are
docs/system-design/ diagrams
localstack/        local AWS init scripts
```

## Docs

- [`TECHSTACK.md`](TECHSTACK.md) — stack, decisions, open questions
- [`docs/schema.md`](docs/schema.md) — database schema and the change-detection strategy
- [`docs/api-spec.md`](docs/api-spec.md) — API contract
- [`docs/input-spec.md`](docs/input-spec.md) — what the prediction engine consumes
- [`docs/adr/`](docs/adr/) — architecture decisions, including the ones that were later overturned

## License

None yet. Ask if you want to use something.
