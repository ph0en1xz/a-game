# ADR 0009 — Local validation split: EKS plan-only on LocalStack, Kubernetes learning on k3s

- **Status:** Accepted — **interim**. A real AWS EKS deployment is an explicit stated goal
  (2026-07-28); this ADR governs local validation *until* that happens, and is superseded on
  the day the cluster layer is applied against a real AWS account. Everything below is written
  to be thrown away cleanly, not lived with.
- **Date:** 2026-07-28
- **Related:** ADR 0002 (EKS for learning), ADR 0006 (CI/CD on GitHub Actions), ADR 0008 (AI
  platform layer — its workloads land on whichever cluster this decision selects)
- **Deciders:** Mario (Nexoro Tech)

## Context

The Terraform infra track is built and validated against **LocalStack Community**. The network
layer (16 resources) applied there successfully. The cluster layer then hit a hard wall: the
first `apply` returned

```
Error: creating EKS Cluster (a-game-eks): api error InternalFailure:
API for service 'eks' not yet implemented or pro feature
```

LocalStack Community does not emulate EKS at all — it is a Pro feature. This is not a defect in
the Terraform: the provider serialized a valid `CreateCluster` request, meaning the IAM role,
the `terraform_remote_state` read of the network layer, and the subnet wiring were all correct.
The apply stopped at the mock's edge, leaving state consistent (cluster IAM role and policy
attachment created, cluster absent).

A second, more important limitation was already known and is independent of EKS support:
**LocalStack is a control-plane mock and moves no real packets.** VPCs, NAT gateways, route
tables, and security groups exist there as in-memory bookkeeping. Since networking depth —
AWS VPC *and* Kubernetes — is the explicit learning priority of this project, a control-plane
mock can never validate the thing that matters most.

Four options were considered: plan-only EKS; LocalStack Pro (which backs its EKS mock with a
real k3s, on a trial clock and a subscription); local k3s/kind for the Kubernetes half; and
real AWS EKS (~$0.10/hr control plane plus nodes and NAT).

## Decision

Split local validation across two complementary tracks. Neither costs anything.

### 1. The Terraform cluster layer is plan-only

The EKS layer is written in full — cluster, node group IAM, managed node group, OIDC provider
for IRSA, and outputs — and its definition of done is a clean `terraform fmt` + `validate` +
`plan`. It is **never applied** against LocalStack. This is the same gate CI runs under ADR
0006, so plan-only costs no extra machinery.

No mechanism enforces the no-apply rule, and none is added: any `apply` fails loudly at the
same 501 the moment it reaches the cluster resource, so the rule is self-policing.

Accepted limitation: resources that reference cluster attributes — notably the OIDC provider
reading `aws_eks_cluster.cluster.identity[0].oidc[0].issuer` — plan as `(known after apply)`.
Those wirings are syntactically and graph-validated but not value-verified until real AWS.

The two IAM resources already in state from the failed apply are valid and are left in place.

### 2. Kubernetes learning moves to k3s in WSL2

All cluster-side work — manifests, Services, Ingress, NetworkPolicy, and later the ADR 0008 AI
platform workloads — runs against a local **k3s** cluster rather than any AWS emulation.

k3s is chosen over kind specifically for the networking priority: k3s ships an embedded
kube-router NetworkPolicy controller, so policies are actually **enforced** and can be observed
passing and failing. kind's default CNI silently ignores NetworkPolicy, which would produce
policies that appear to work while proving nothing — the worst possible outcome for a learning
exercise whose whole point is the deferred egress lockdown.

### 3. LocalStack's scope is now explicit

LocalStack remains the target for **Terraform graph, provider schema, state segmentation, and
plan correctness** only. It is not, and was never, a validator of runtime network behaviour.

## Staying apply-ready for real EKS

Because the real deployment is a stated goal, neither track may accumulate local-only
shortcuts that have to be unpicked later. Three specific hazards, all of which are cheap to
avoid now and expensive to discover on deploy day:

1. **LocalStack config must not be hardcoded into the provider.** `infrastructure/cluster/
   providers.tf` currently pins `access_key = "test"`, `secret_key = "test"`, and an
   `endpoints` block aimed at `localhost:4566`. Against real AWS every one of those is wrong.
   Before the first real apply this has to become conditional — a `localstack_endpoint`
   variable that is empty for real AWS, driving a `dynamic "endpoints"` block and nulling the
   dummy credentials so the normal AWS credential chain takes over. The same applies to
   `data.tf`'s remote-state config and the `backend "s3"` block, which cannot use variables
   at all and will need `-backend-config` or a separate backend file per target.

2. **k3s conveniences are not EKS behaviours.** k3s ships Traefik as its default Ingress
   controller, `local-path` as its default StorageClass, and klipper-lb to make
   `type: LoadBalancer` work on a laptop. EKS has none of these — it uses the AWS Load
   Balancer Controller, the EBS CSI driver with `gp3`, and real ELBs. Manifests must therefore
   name their `ingressClassName` and `storageClassName` explicitly rather than relying on
   whatever the cluster defaults to, or they will bind to the wrong thing on EKS.

3. **NetworkPolicy is enforced by different components, and EKS does not enforce it by
   default.** k3s embeds kube-router, so policies bite immediately. On EKS the VPC CNI only
   enforces NetworkPolicy when the network policy agent is explicitly enabled (or Calico is
   installed) — otherwise every policy is accepted by the API server and silently ignored.
   This is the most dangerous of the three: the egress lockdown from ADR 0008 would appear
   correct in `kubectl get networkpolicy` while allowing all traffic. Enabling enforcement is
   a required part of the EKS cluster layer, not an afterthought.

Additionally, IRSA has no k3s equivalent. Service-account IAM annotations written locally are
inert and only take effect on EKS, so that wiring stays unverified until the real apply.

**Cost, when the real deploy happens:** the EKS control plane runs ~$0.10/hr (~$73/mo) whether
or not anything is scheduled on it, plus nodes and the NAT gateway (~$32/mo). Treated as a
short deliberate window with a verified `terraform destroy`, not a standing environment.

## Consequences

- **Positive:** the two tracks are complementary rather than redundant — Terraform exercises
  the resource graph and layered remote state; k3s exercises the packets. Together they cover
  more than either LocalStack Pro or real AWS would alone at this stage.
- **Positive:** NetworkPolicy work becomes genuinely verifiable, which unblocks the deferred
  egress-lockdown item and the ADR 0008 claim that the model gateway is the single egress hop.
- **Positive:** zero cost, no trial clock, no credit card.
- **Negative:** the EKS layer is never executed, so real-world failures — IAM propagation
  delays, subnet capacity, control-plane provisioning errors, node bootstrap — go undiscovered
  until it runs against real AWS.
- **Negative:** k3s is not EKS. Node bootstrap, the AWS VPC CNI (pod IPs from real VPC
  addresses), IRSA, and the AWS load balancer controller have no local equivalent and remain
  design-level knowledge only.
- **Neutral:** revisit if the project ever runs against real AWS for a short, deliberate
  window — the plan-only Terraform is written to be apply-ready when that happens.
