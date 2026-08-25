# A-Game

A football predictions service: it ingests match results, rates teams with Elo, converts those
ratings into scoreline probabilities with a Poisson model, and has a language model write the
preview text that explains what the numbers say. Six workloads on Kubernetes, a default-deny
network, every container non-root, all model traffic through a gateway that owns the credentials
and the cost controls.

## What it's for

Predictions have a commercial shape: bookmakers publish odds, a model publishes probabilities, and
the gap between the two is where value sits. The system is built around the properties that make
that gap trustworthy rather than just computable.

**Probabilities, not tips.** Elo ratings feed a Poisson scoreline model, so every output is a
distribution with arithmetic behind it and no opinion in it.

**Auditable after the fact.** Predictions are persisted permanently and tagged with the model
version that produced them, so accuracy is measured rather than claimed. Most prediction products
cannot tell you how they did last season.

**No invented statistics.** The maths is deterministic and lives in code. The language model only
phrases the output — it never sources a number. In a domain adjacent to real money, a narration
layer that can hallucinate a stat is a liability, not a feature.

**Cost scales with new information, not with elapsed time.** Ingestion is change-gated: a run that
finds nothing new publishes nothing, and the expensive half of the pipeline stays asleep. Fixtures
arrive in weekend and midweek clusters, so most runs have nothing to say.

**Predictable read latency.** Nothing is computed on request. The API serves what was already
written, so there are no pending states, no polling, and no request that can take four seconds.

Value betting — comparing model probability against published odds — is the commercial endpoint.
It currently returns `null` because odds feeds are paywalled, but the schema and the read path are
already shaped for it.

![Architecture](docs/system-design/a-game-architecture.svg)

More views — network topology, the AI platform layer, IRSA, the request flows — are in
[`docs/system-design/`](docs/system-design/).

## How it works

Three services, one pipeline:

**worker** (`a-game-worker`) — a CronJob that runs daily at 06:00 UTC. It fetches from
[football-data.org](https://www.football-data.org), upserts the facts into Postgres, and publishes a
"data ready" job *only if the upsert actually changed a row*. A run that changes nothing publishes
nothing and exits. That constraint is the whole design
([ADR 0007](docs/adr/0007-ingestion-cadence-daily-change-gated.md)) — firing the prediction pipeline
on byte-identical inputs is spending money to recompute yesterday.

**brain** (`a-game-brain`) — consumes that job, recomputes ratings and probabilities for upcoming
fixtures, has the model narrate them, writes results back to Postgres and warms Redis.

**api** (`a-game-api`) — four read-only `GET` endpoints serving what the brain already computed.
Contract in [`docs/api-spec.md`](docs/api-spec.md).

Alongside them: Postgres, Redis, RabbitMQ, and a LiteLLM gateway that every model call goes through.

## Platform

### Network

The namespace is closed in both directions by two empty NetworkPolicies, then opened path by path.
NetworkPolicy is default-*allow* — a pod is wide open until some policy selects it, the opposite of
a security group — so the empty policies are what drag every pod, including new ones, into the
firewalled set.

DNS gets an explicit hole to CoreDNS in `kube-system`, because name resolution is ordinary
pod-to-pod traffic and is blocked like anything else. Miss it and every hostname fails in a way that
looks exactly like the database being down.

Every other rule names both ends: the client's egress *and* the server's ingress. Selectors are pod
labels, never Service IPs, because kube-proxy rewrites the Service IP to a pod IP before policy is
evaluated — and policy is evaluated against the endpoint, so a rule written against a Service port
silently matches nothing. The two `ipBlock` rules are the ones with no pod to select: the worker
reaching football-data.org, and the gateway reaching the model providers. The second is
`0.0.0.0/0:443` rather than named ranges, because the providers sit behind CDNs that rotate
addresses — so the enforced boundary there is *which pod may egress*, not where it may go.

Rules are verified by connection, not by a clean `kubectl apply`. A valid policy with the wrong port
number applies without complaint and fails at runtime with no log line anywhere.

Full annotated policy set in [`k8s/50-networkpolicies.yaml`](k8s/50-networkpolicies.yaml).

### Container security

Every workload runs as a non-root user with `allowPrivilegeEscalation: false` and all Linux
capabilities dropped. The uids come from the images, not from guesswork — 1000 for the three
services built here, 70 for `postgres:16-alpine`, 999 for redis, rabbitmq and litellm.

The three images under our control also set `readOnlyRootFilesystem: true`, with an `emptyDir`
mounted at `/tmp` for what Python still needs to write. The four third-party images don't, because
their write paths can't be audited.

Service accounts set `automountServiceAccountToken: false`. Nothing here talks to the Kubernetes
API, so nothing gets a token worth stealing.

Secrets are created imperatively and never committed. Kubernetes Secrets are base64, not encryption
— on EKS these move to SSM Parameter Store fronted by External Secrets, with an IRSA role scoped to
a single parameter path.

### AI platform

Model calls don't go straight to the provider. They go through a LiteLLM gateway
([ADR 0008](docs/adr/0008-ai-platform-layer.md)) that owns the API keys, so credentials exist in
Secrets mounted into exactly one pod and the application services hold none. It also gives one
place to set retries, timeouts, per-model routing and spend limits without redeploying anything
downstream — model access treated as infrastructure with a bill attached, rather than as a library
import.

Two providers sit behind it: Claude Haiku as the primary route, OpenAI as its configured fallback.
A single model behind a gateway is just a proxy — the routing and failover are the part worth
building, and a fallback nobody has watched fire is indistinguishable from one that doesn't work.
The application asks for a model alias and never learns which provider answered.

That does mean one pod now holds two providers' keys. The blast radius is still a single pod, and
a gateway per provider would double the workload count to narrow it no further.

Tracing (Langfuse) and eval gating in CI are the next pieces and aren't built yet. The eval
harness is the flagship of the whole layer, not a checkbox: a golden fixture set committed to
the repo, a fact-checker that turns "the AI never invents stats" into a CI assertion, an
LLM-as-judge whose own prompt is versioned, and a regression gate — a prompt change goes
through a PR like a code change, and can fail the build like one. The same harness then drives
a model-comparison report (quality, latency, cost per preview, per route), so the choice of
primary and fallback is measured rather than configured. Planned behind those: a tool-using
match-analyst agent, a self-hosted ~3B model as a third gateway route at CPU scale, and an AI
threat-model doc (ADR 0008, 2026-08-05). Deliberate non-goals: fine-tuning, chatbots,
semantic caching.

### Infrastructure

Terraform in three layers — network, cluster, app — with separate state per layer, so a mistake in
one can't take down another.

The network layer runs against LocalStack and applies cleanly. The cluster layer is deliberately
plan-only: LocalStack Community doesn't emulate EKS, and pretending otherwise would be theatre
([ADR 0009](docs/adr/0009-local-validation-eks-plan-only-k3s-for-kubernetes.md)). That split
separates two questions that fail in different ways — is the Terraform correct, and are the
manifests correct — and tests each where it can actually be answered. Manifests are enforced on a
real k3s cluster via k3d.

## How it was built

Decisions are argued out in [`docs/adr/`](docs/adr/) before they're implemented, and the reasoning
is kept even when the conclusion is later reversed — ADR 0004 overturns an earlier language choice
and both are still in the tree, because the argument that turned out to be wrong is the part worth
reading.

Manifests are hand-written and applied in numeric order. No Helm: every security control is visible
in the file that declares the workload rather than inherited from a chart's defaults, which is what
makes the network and container posture above reviewable at all.

Constraints are chosen before code. The change-gate, the precomputed read path and the
credential-isolating gateway are all decisions recorded with their trade-offs, not optimisations
discovered later.

Behaviour is verified rather than assumed — network paths proven by connection from a labelled pod,
container users confirmed against the running process, Terraform validated by plan. Several entries
in the section below are the direct result of that discipline finding something a green deploy had
hidden.

## Decisions worth reading

- **[ADR 0009](docs/adr/0009-local-validation-eks-plan-only-k3s-for-kubernetes.md) — validate
  locally, keep EKS plan-only.** Splits Terraform correctness from Kubernetes correctness and solves
  each where it's testable.
- **[ADR 0007](docs/adr/0007-ingestion-cadence-daily-change-gated.md) — daily, change-gated
  ingestion.** The pipeline fires on changed rows, not on a schedule, so operating cost tracks new
  information.
- **[ADR 0005](docs/adr/0005-v1-service-architecture.md) — three services, one broker.** The API
  never computes. Everything it serves was precomputed by the brain, which is what makes read
  latency predictable.
- **[ADR 0004](docs/adr/0004-language-switch-python.md) — switch to Python.** Reverses an earlier
  decision. The prediction engine is numerical work and the ecosystem argument won.
- **[ADR 0008](docs/adr/0008-ai-platform-layer.md) — an AI platform layer, not an SDK call.**
  Gateway, tracing, and — as the flagship — evals in CI; plus an agent, a self-hosted route,
  and a threat model (2026-08-05 amendment).

## Things that broke, and why

- **EKS silently ignores NetworkPolicy.** Without the VPC CNI policy agent or Calico, every policy
  in this repo is accepted by the API server and enforced by nothing. The worst failure mode there
  is: no error, no event, just an isolation guarantee that doesn't exist.
- **Postgres wouldn't start with capabilities dropped.** Its entrypoint starts as root, chowns the
  data directory, then drops to the postgres user with `su-exec`. `drop: ALL` kills both steps. The
  fix wasn't handing capabilities back — it was running as uid 70 from the start, so the entrypoint
  skips its root branch entirely and needs none.
- **A NetworkPolicy with the wrong port applies cleanly and fails silently.** Schema validation
  catches a misspelled field. It cannot catch a port number that's valid and wrong, and the
  resulting refusal is indistinguishable from a dead server.
- **`readOnlyRootFilesystem` and `emptyDir` at `/tmp` are a pair.** Setting one without the other
  produces a crash loop at import time, well before any application code runs.
- **The LiteLLM config is a volume, not `envFrom`.** `config.yaml` isn't a legal environment
  variable name, so `envFrom: configMapRef` skips it and logs an `InvalidVariableNames` event most
  people never look at. Volume-mounted ConfigMaps also update live; env vars are frozen at pod start.

## Cost

Currently zero — k3d and LocalStack run on a laptop.

The EKS numbers that matter: the control plane is $0.10/hour whether the cluster is empty or busy,
about $73/month. NAT Gateways are the line item that hurts — roughly $32/month each plus data
processing, and private-subnet pods need one per AZ to reach football-data.org and the model
provider.

So the plan is short, deliberate runs with a full teardown afterwards. Teardown has its own trap:
`type: LoadBalancer` Services and PVC-backed EBS volumes are created by Kubernetes rather than
Terraform, so `terraform destroy` leaves them behind, billing quietly.

## Stack

Python 3.12 with FastAPI and pydantic, managed by `uv`. PostgreSQL is the only system of record —
raw payloads land in JSONB alongside typed columns, and predictions are kept permanently and tagged
with the model version. RabbitMQ carries jobs between services. Redis is a read-through cache and
nothing else; you could delete it and the design would still hold.

Everything is containerized: multi-stage builds, non-root, no package manager in the final image.
Kubernetes manifests are hand-written and applied in numeric order. Terraform is layered with
isolated state. Model access goes through LiteLLM.

The full breakdown, including what's decided and what isn't, is in
[`TECHSTACK.md`](TECHSTACK.md).

## Status

The platform is ahead of the application.

Working — the cluster, the network policies, the hardened workloads, the LiteLLM gateway, the
Terraform network layer, the schema, and a worker that fetches competitions and seasons into
Postgres on a schedule.

Not done:

- Match ingestion — the change gate and the fixture upsert
- The Elo/Poisson engine is written but not wired to anything
- Model narration is written but not wired either
- The predictions half of the schema isn't modelled yet
- CI exists for the three services but is unproven; the Terraform plan job from
  [ADR 0006](docs/adr/0006-cicd-github-actions-gitops-later.md) isn't built
- GitOps is running. Argo CD v3.5.1 reconciles `k8s/` and manages itself from `k8s/argocd/`; both
  `Application`s are Synced and Healthy. Auto-sync is on for `a-game`; prune and self-heal are
  still off
- No metrics, logs or alerting
- Nothing has run on real AWS yet
- No auth on the API
- No odds feed, so value bets are `null`

Single replica per workload, one node, no HA — a deployment topology choice at this stage, not an
architectural constraint.

## Running it locally

You'll need Docker, [k3d](https://k3d.io), `kubectl`, and [uv](https://docs.astral.sh/uv/).

```bash
# supporting services (LocalStack, Postgres)
docker compose up -d

# cluster
k3d cluster create a-game
kubectl apply -f k8s/00-namespace.yaml
```

### Secrets — create these before `kubectl apply -f k8s/`

Nothing in `k8s/` creates a Secret. They're made imperatively and never committed, so the
manifests reference ten Secrets that must already exist in the `a-game` namespace. Apply the
manifests without them and pods sit in `CreateContainerConfigError` naming the missing key —
which is a clearer failure than most, but only if you know to look for it.

**Key names matter as much as Secret names.** Most are consumed with `envFrom`, so every key
becomes an environment variable verbatim and a typo'd key is silently absent rather than an error.

| Secret | Keys | Used by |
|---|---|---|
| `db-credentials` | `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | postgres, api, brain, worker, and the Langfuse bootstrap Job (as the superuser) |
| `rabbitmq-credentials` | `RABBITMQ_DEFAULT_USER`, `RABBITMQ_DEFAULT_PASS` | rabbitmq, brain, worker |
| `sports-api-credentials` | `SPORTS_API_KEY` | worker — your football-data.org key |
| `anthropic-credentials` | `ANTHROPIC_API_KEY` | litellm only. The brain never receives it |
| `openai-credentials` | `OPENAI_API_KEY` | litellm only, for the fallback model |
| `minio-credentials` | `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD` | minio, langfuse web + worker |
| `clickhouse-credentials` | `CLICKHOUSE_USER`, `CLICKHOUSE_PASSWORD` | clickhouse, langfuse web + worker |
| `lang-db-credentials` | `PGUSER`, `PGPASSWORD` | the `langfuse` Postgres role — created by the bootstrap Job, then used by langfuse web + worker |
| `langfuse-credentials` | `SALT`, `ENCRYPTION_KEY` | langfuse web + worker. Must be **byte-identical** across both |
| `langfuse-web-secrets` | `NEXTAUTH_SECRET`, `LANGFUSE_INIT_ORG_ID`, `LANGFUSE_INIT_PROJECT_ID`, `LANGFUSE_INIT_PROJECT_PUBLIC_KEY`, `LANGFUSE_INIT_PROJECT_SECRET_KEY`, `LANGFUSE_INIT_USER_EMAIL`, `LANGFUSE_INIT_USER_PASSWORD` | langfuse web. The two `PROJECT_*_KEY` values are also read by litellm to send traces |

The application secrets:

```bash
kubectl create secret generic db-credentials -n a-game \
  --from-literal=POSTGRES_USER='postgres' \
  --from-literal=POSTGRES_PASSWORD='<choose one>' \
  --from-literal=POSTGRES_DB='a_game_db'

kubectl create secret generic rabbitmq-credentials -n a-game \
  --from-literal=RABBITMQ_DEFAULT_USER='<choose one>' \
  --from-literal=RABBITMQ_DEFAULT_PASS='<choose one>'

kubectl create secret generic sports-api-credentials -n a-game \
  --from-literal=SPORTS_API_KEY='<your football-data.org key>'

kubectl create secret generic anthropic-credentials -n a-game \
  --from-literal=ANTHROPIC_API_KEY='<your Anthropic key>'

kubectl create secret generic openai-credentials -n a-game \
  --from-literal=OPENAI_API_KEY='<your OpenAI key>'
```

The Langfuse observability stack. `ENCRYPTION_KEY` must be 32 bytes of hex; `SALT` and
`NEXTAUTH_SECRET` 32 bytes of base64 — Langfuse rejects anything else at boot:

```bash
kubectl create secret generic minio-credentials -n a-game \
  --from-literal=MINIO_ROOT_USER='<choose one>' \
  --from-literal=MINIO_ROOT_PASSWORD='<choose one, min 8 chars>'

kubectl create secret generic clickhouse-credentials -n a-game \
  --from-literal=CLICKHOUSE_USER='default' \
  --from-literal=CLICKHOUSE_PASSWORD='<choose one>'

kubectl create secret generic lang-db-credentials -n a-game \
  --from-literal=PGUSER='langfuse' \
  --from-literal=PGPASSWORD='<choose one>'

kubectl create secret generic langfuse-credentials -n a-game \
  --from-literal=SALT="$(openssl rand -base64 32)" \
  --from-literal=ENCRYPTION_KEY="$(openssl rand -hex 32)"

kubectl create secret generic langfuse-web-secrets -n a-game \
  --from-literal=NEXTAUTH_SECRET="$(openssl rand -base64 32)" \
  --from-literal=LANGFUSE_INIT_ORG_ID='a-game' \
  --from-literal=LANGFUSE_INIT_PROJECT_ID='a-game' \
  --from-literal=LANGFUSE_INIT_PROJECT_PUBLIC_KEY='<choose one>' \
  --from-literal=LANGFUSE_INIT_PROJECT_SECRET_KEY='<choose one>' \
  --from-literal=LANGFUSE_INIT_USER_EMAIL='<your email>' \
  --from-literal=LANGFUSE_INIT_USER_PASSWORD='<choose one, min 8 chars>'
```

`CLICKHOUSE_USER` should stay `default` — Langfuse runs its schema migrations as that user, and a
restricted one fails at migration time rather than at connect time. `LANGFUSE_INIT_PROJECT_*_KEY`
are values you pick; Langfuse provisions the org, project, user and API key pair on first boot,
which is why no key generation step exists.

Then apply the rest:

```bash
kubectl apply -f k8s/
```

Order matters within `k8s/` too — the numeric prefixes are the apply order. `10-serviceaccounts.yaml`
must land before any workload that names a ServiceAccount, and `21-langfuse-db.yaml` (the Job that
creates the `langfuse` role and database) must **complete** before `93`/`94` start, since Langfuse
runs migrations against that database on boot.

On EKS these become AWS Secrets Manager entries synced by External Secrets Operator. The manifests
don't change — they reference a Secret object either way.

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

### GitOps — Argo CD

Argo CD reconciles `k8s/` against the cluster, so Git becomes the source of truth instead of
whatever was last applied by hand. It lives in its own `argocd` namespace and its manifests are in
`k8s/argocd/`.

The pinned v3.5.1 install manifest is vendored at `k8s/argocd/02-install-argocd.yaml` rather than
curled at install time, so Argo CD's own workloads are visible in Git and can be managed by Argo CD.
Bootstrap it:

```bash
kubectl apply -f k8s/argocd/01-namespace-argocd.yaml
kubectl apply -n argocd --server-side --force-conflicts -f k8s/argocd/02-install-argocd.yaml
kubectl apply -f k8s/argocd/05-application-argocd.yaml
```

`--server-side` is not optional. Client-side apply writes the whole manifest into the
`last-applied-configuration` annotation, and the `applicationsets.argoproj.io` CRD exceeds the
262,144-byte limit — you get `metadata.annotations: Too long`. The same is why the `argocd`
Application carries `syncOptions: [ServerSideApply=true]` and CI's dry-run step passes
`--server-side`.

Re-vendoring on a version bump means re-applying the local edits in that file: resource requests
and limits, which upstream does not ship.

There's an `manifests/ha/install.yaml` variant too. Skip it — HA runs Redis as a three-node cluster
plus extra replicas of every component, which on a single k3d node costs real memory and buys
availability you'd never observe.

Seven workloads come up: `application-controller` (a StatefulSet), `applicationset-controller`,
`dex-server`, `notifications-controller`, `redis`, `repo-server`, and `server`. Budget roughly
400–500Mi. The controller is usually last to settle.

To reach the UI, port-forward `svc/argocd-server` — its service port is 443, and pick something
other than 8080 locally because the ingress already holds that. The initial admin password is in a
Secret called `argocd-initial-admin-secret` under key `password`. Change the password, then delete
that Secret; it's meant to be transient.

Once that Secret is gone the password is unrecoverable — it lives only as a bcrypt hash in
`argocd-secret`. To reset it, generate a hash with `argocd account bcrypt --password '<new>'` and
patch `argocd-secret`'s `stringData` with both `admin.password` and `admin.passwordMtime`. The mtime
invalidates issued tokens, so existing CLI sessions fail with `account password has changed since
token issued` until you log in again.

A port-forward binds to one pod and never re-resolves, so any pod replacement kills it with
`lost connection to pod`. That is not a NetworkPolicy problem — a policy drop hangs and times out,
while a dead forward refuses instantly.

**Sync settings, and why they start conservative.** Both `Application`s began with no `syncPolicy`
block at all, which means manual sync, no prune, no self-heal. Auto-sync came first, on `a-game`
only, as `syncPolicy.automated: {}` — the empty braces matter, because `automated` with no fields
turns on auto-sync while leaving prune and self-heal off. Prune lets Argo CD delete anything on the
cluster that isn't in Git — the right end state, and the wrong thing to have enabled during the
first sync, when the diff still contains drift you haven't looked at. Self-heal reverts manual
changes automatically, which will silently undo a `kubectl edit` and cost you an hour wondering
why. Turn both on after the first few syncs are boring.

**Hook resources that linger are flagged for pruning forever.** Argo CD stamps its tracking
annotation on everything it applies, hooks included, but hooks are excluded from desired state — so
a hook resource still on the cluster looks like something Git no longer declares. Deleting it does
not help; the hook recreates it stamped. The fix is
`argocd.argoproj.io/hook-delete-policy: HookSucceeded`, which is why both the ConfigMap and the Job
in `21-langfuse-db.yaml` carry it.

**`k8s/` is not synced recursively, and that's deliberate.** Argo CD defaults to non-recursive
directory scanning, so an Application on `k8s/` sees the numbered manifests and skips the
subdirectories — including `k8s/test/nginx.yaml`, which you do not want in the cluster. The same
non-recursion applies in `ci-k8s-manifests.yml`: both Python checkers glob `*.yaml` in one
directory and the dry-run has no `-R`, so anything under `k8s/argocd/` needs its own explicit
invocation in that workflow — which it now has: both checkers and the dry-run run twice, once per
directory. One gap remains by construction: because each checker globs a single directory, a policy
pair that spans `k8s/` and `k8s/argocd/` cannot be validated by it. Cross-namespace pairing stays
a manual check.

**Argo CD is the one thing that can't be installed by Argo CD.** You bootstrap it with `kubectl
apply`, and only then can it manage everything else — itself included. That chicken-and-egg step is
expected, and it applies to the `argocd` Application too: merging it to `main` changes nothing,
because until it exists nothing is watching `k8s/argocd/`. Apply it by hand once; it self-manages
after that.

There is no separate app-of-apps root manifest. `k8s/argocd/` contains both Application files, so
the `argocd` Application manages the `a-game` Application as one of its resources — the same
pattern, arrived at without an extra layer.

Once Argo CD is running and syncing, `kubectl apply -f k8s/` is drift, not deployment. That's the
whole point, but it is a real change to how you work day to day.

## Layout

```
a-game-api/         FastAPI read API
a-game-brain/       prediction engine + model narration
a-game-worker/      the worker CronJob, plus the schema DDL in postgres/
k8s/                manifests, applied in numeric order
k8s/argocd/         Argo CD install and Application manifests
infrastructure/     Terraform, layered: network → cluster → app
.github/workflows/  CI per service, plus manifest validation
docs/adr/           why things are the way they are
docs/system-design/ diagrams
localstack/         local AWS init scripts
```

## Docs

- [`TECHSTACK.md`](TECHSTACK.md) — stack, decisions, open questions
- [`docs/schema.md`](docs/schema.md) — database schema and the change-detection strategy
- [`docs/api-spec.md`](docs/api-spec.md) — API contract
- [`docs/input-spec.md`](docs/input-spec.md) — what the prediction engine consumes
- [`docs/system-design/`](docs/system-design/) — architecture and flow diagrams
- [`docs/adr/`](docs/adr/) — architecture decisions, including the ones later overturned

## License

None yet. Ask if you want to use something.
