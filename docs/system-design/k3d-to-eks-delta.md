# k3d → EKS delta

What changes when the cluster stops being k3d on a laptop and becomes EKS in a VPC. Written
2026-08-17, while the reasoning behind each local value is still fresh.

Scope: the Langfuse stack (`21-langfuse-db.yaml`, `50-networkpolicies.yaml`, `91-minio.yaml`,
`92-clickhouse.yaml`, `93-langfuse-web.yaml`, `94-langfuse-worker.yaml`) plus the parts of the
existing app it touches. Per ADR 0009 the EKS Terraform is plan-only, so nothing here has been
executed — this is the delta to work through when that changes.

---

## 1. The four that fail quietly

These don't error. They either silently do nothing or fail in a way that points somewhere else.

### NetworkPolicy stops being enforced

k3s enforces NetworkPolicy in-process, which is why a blocked connection here is refused instantly
rather than timing out. The Amazon VPC CNI **ignores every NetworkPolicy object** unless the network
policy agent is explicitly enabled on the addon, or you replace the CNI with Calico or Cilium.

Nothing warns you. Every policy in `50-networkpolicies.yaml` applies cleanly, `kubectl get netpol`
lists them all, and every pod can reach every other pod. A default-deny posture becomes an
allow-all posture, and the only way to notice is to test a connection you expect to be blocked.

Verify with a negative test after the migration — exec into `a-game-brain` and try to reach
ClickHouse. It must fail.

The CIDR literals in that file are k3d and flannel addresses. On EKS pods carry real VPC addresses
from the subnet CIDRs, so any `ipBlock` rule needs rewriting. `podSelector` and
`namespaceSelector` rules carry over unchanged, which is an argument for preferring them.

### `local-path` does not exist

Every PVC in `91-minio.yaml` and `92-clickhouse.yaml` binds through k3d's `local-path` provisioner.
On EKS that StorageClass isn't there, so the PVCs sit `Pending` forever and the StatefulSets never
schedule. No error on apply — just pods that never start.

Replacement is `gp3` through the EBS CSI driver, which also means the driver has to be installed and
its controller needs an IAM role. Sizes stop being free: decide them deliberately rather than
inheriting whatever k3d handed out.

ClickHouse is the one to think about. It's write-heavy and the default gp3 baseline of 3000 IOPS is
the floor, not a target.

### Hand-created Secrets vanish

`lang-db-credentials`, `minio-credentials`, `clickhouse-credentials`, `langfuse-credentials` and
`langfuse-web-secrets` were all created with `kubectl create secret`. They exist in exactly one
place — the cluster — and are invisible to git, to review, and to anyone rebuilding the cluster.

Per the project convention that becomes SSM Parameter Store or Secrets Manager, synced into real
Secret objects by External Secrets Operator. Not the Secrets Store CSI driver on its own: CSI mounts
files, and every `secretRef` / `secretKeyRef` in these manifests needs an actual Secret object to
reference.

The upside is that rotation becomes one write instead of five. Today `SALT` and `ENCRYPTION_KEY`
must stay byte-identical across web and worker by hand.

### Static keys become IRSA

Langfuse currently authenticates to MinIO as **root**, via `MINIO_ROOT_USER` and
`MINIO_ROOT_PASSWORD` mapped in through four `secretKeyRef` entries per container. That's an
accepted compromise for a single-tenant local blob store. It has no place in a VPC.

On S3 the four `LANGFUSE_S3_*_ACCESS_KEY_ID` / `_SECRET_ACCESS_KEY` variables get **deleted**, not
replaced. Langfuse uses the AWS SDK, which falls back to the default credential chain — and with
IRSA that chain resolves to the web identity token projected into the pod.

Note that `automountServiceAccountToken: false` on all seven ServiceAccounts does **not** block
this. IRSA works through a separate projected volume injected by the pod identity webhook, distinct
from the default SA token that flag governs. Worth confirming on the first pod that needs it rather
than taking it on faith.

---

## 2. Component swaps

| Local | EKS | Notes |
|---|---|---|
| MinIO StatefulSet | S3 bucket | Delete `91-minio.yaml` entirely. Bucket per environment. |
| `a-game-postgres` StatefulSet | RDS Postgres | `DATABASE_HOST` and `PGHOST` change; nothing else. |
| ClickHouse StatefulSet | ClickHouse on EBS, or ClickHouse Cloud | Open decision — see below. |
| `a-game-redis` StatefulSet | ElastiCache, or keep in-cluster | Open decision. |
| Traefik | AWS Load Balancer Controller | Different IngressClass, different annotations. |
| k3d local-path | EBS CSI + gp3 | See above. |

**ClickHouse is the one genuinely open question.** Self-hosting it on EKS means owning EBS sizing,
backups, and version upgrades for a database nobody on the team has operated. ClickHouse Cloud
removes all of that and changes only `CLICKHOUSE_URL`, `CLICKHOUSE_MIGRATION_URL` and the
credentials — the manifests barely notice. Worth an ADR before the migration rather than during it.

**Redis is less obvious than it looks.** It stopped being a removable cache when Langfuse started
using db 1 as its ingestion queue (ADR 0008 §Amendments). ElastiCache is the managed answer, but
BullMQ is sensitive to Redis behaviour and ElastiCache's cluster mode is not a drop-in. Single-node
ElastiCache, or keeping Redis in-cluster, are both defensible.

---

## 3. The Ingress comes back

`langfuse-web` deliberately has no Ingress locally — it's cluster-internal and reached over
`kubectl port-forward svc/a-game-lang-web 3000:3000`, which is exactly what
`NEXTAUTH_URL=http://localhost:3000` matches.

On EKS a UI behind a VPN needs a real entry point:

```yaml
spec:
  ingressClassName: alb
  rules:
    - host: langfuse.<internal-domain>
```

with `alb.ingress.kubernetes.io/scheme: internal` — private subnets, VPC-only addresses, reachable
over VPN or a bastion and from nowhere else. Write the annotation explicitly rather than relying on
the controller default, so the intent is on the page.

Then `NEXTAUTH_URL` becomes that hostname over HTTPS. Get it wrong and sign-in redirects land on the
wrong origin, which presents as a Langfuse bug rather than a config one.

`a-game-api` already has an Ingress and needs the same treatment, except `internet-facing` — it has
real external clients. Note that it currently claims `/` on **all** hosts with no `host` field. Two
Ingresses doing that collide, so give both a hostname during the migration.

---

## 4. Per-file summary

| File | Change |
|---|---|
| `21-langfuse-db.yaml` | `PGHOST` → RDS endpoint. Shape is unchanged — it's a pod reaching a network endpoint with a Secret, which was the whole point of using a Job over an exec script. |
| `50-networkpolicies.yaml` | Confirm enforcement is on. Rewrite every `ipBlock` CIDR. Add rules for the ALB controller reaching api and langfuse-web. |
| `91-minio.yaml` | Delete. |
| `92-clickhouse.yaml` | `storageClassName: gp3`, sized deliberately. Or delete if ClickHouse Cloud wins. |
| `93-langfuse-web.yaml` | Drop the four S3 credential env vars, `_ENDPOINT` and `_FORCE_PATH_STYLE`. Real `_REGION`. IRSA annotation on `a-game-lang-web-sa`. New Ingress. Real `NEXTAUTH_URL`. |
| `94-langfuse-worker.yaml` | Same S3 changes. IRSA annotation on `a-game-lang-worker-sa`. No Ingress — nothing dials in to it. |
| `90-litellm.yaml` | The `0.0.0.0/0:443` egress rule can narrow now that the NAT gateway and route tables are known. |
| `10-serviceaccounts.yaml` | IRSA `eks.amazonaws.com/role-arn` annotations on the SAs that touch AWS. |

---

## 5. What doesn't change

Worth stating, because it's most of it.

Every Deployment and StatefulSet keeps its shape. Probes, resource requests, security contexts,
labels, selectors and the Service definitions all carry over untouched. The env contract is
identical apart from the S3 block. The Job stays a Job.

`db-credentials` still exists as a Secret object — External Secrets changes where it's *sourced*
from, not what the app reads. Per `CLAUDE.md:28` the application always reads from a Kubernetes
Secret, which is what makes the swap config-only.

Resource requests become a cost decision rather than a laptop one. ClickHouse currently requests
512Mi against roughly 1875Mi of idle usage, which makes it the most evictable pod in the namespace
— kubelet ranks Burstable pods by usage over request under memory pressure. Fix that before it
matters on a node you're paying for.

---

## Related

- [`langfuse-trace-network.html`](langfuse-trace-network.html) — the trace path and the policy every hop needs
- [`irsa-flow.html`](irsa-flow.html) — how the token-for-credentials exchange actually works
- [`network-topology.html`](network-topology.html) — the AWS network layer this lands in
- [`../adr/0009-local-validation-eks-plan-only-k3s-for-kubernetes.md`](../adr/0009-local-validation-eks-plan-only-k3s-for-kubernetes.md) — why the split exists
