#!/usr/bin/env python3
"""Find egress rules whose destination has no matching ingress rule.

NetworkPolicy is connection-oriented and both-sided: a connection is allowed
only if the source's egress permits it AND the destination's ingress permits it.
Get one side right and the other wrong and the manifests still apply cleanly -
the failure shows up as a timeout at run time, which is how the Langfuse Job
sat blocked on a dual-sided gap.

Only pairs that can actually break are reported. A destination selected by no
ingress policy at all is not default-deny, so an egress rule pointing at it
needs no counterpart and is skipped. Peers outside the cluster (ipBlock) and in
other namespaces (kube-dns and friends) are skipped for the same reason: there
is no in-repo ingress rule that could match them.

    python3 scripts/check_k8s_policies.py [directory]

Exit 0 if every in-namespace egress pair has its ingress half, 1 otherwise.
"""

import sys
from pathlib import Path

import yaml

ANY_PORT = None


def selector(node):
    """A pod selector reduced to something hashable and comparable."""
    if not node:
        return frozenset()
    return frozenset((node.get("matchLabels") or {}).items())


def ports_of(rule):
    entries = rule.get("ports") or []
    if not entries:
        return [ANY_PORT]
    return [(entry.get("protocol", "TCP"), entry.get("port")) for entry in entries]


def peers_of(rule, key):
    """In-namespace pod peers only - anything else has no in-repo ingress half."""
    for peer in rule.get(key) or []:
        if "ipBlock" in peer or "namespaceSelector" in peer:
            continue
        if "podSelector" in peer:
            yield selector(peer["podSelector"])


def policies(directory: Path):
    for path in sorted(directory.glob("*.yaml")):
        for doc in yaml.safe_load_all(path.read_text()):
            if isinstance(doc, dict) and doc.get("kind") == "NetworkPolicy":
                yield path.name, doc


def main() -> int:
    directory = Path(sys.argv[1] if len(sys.argv) > 1 else "k8s")
    if not directory.is_dir():
        print("not a directory: {}".format(directory), file=sys.stderr)
        return 2

    protected = set()
    allowed = set()
    egress = []
    names = {}

    for filename, doc in policies(directory):
        spec = doc.get("spec") or {}
        subject = selector(spec.get("podSelector"))
        kinds = spec.get("policyTypes") or []
        policy_name = (doc.get("metadata") or {}).get("name")
        names.setdefault(subject, set()).add(policy_name)

        if "Ingress" in kinds:
            protected.add(subject)
            for rule in spec.get("ingress") or []:
                for source in peers_of(rule, "from"):
                    for port in ports_of(rule):
                        allowed.add((subject, source, port))

        if "Egress" in kinds:
            for rule in spec.get("egress") or []:
                for destination in peers_of(rule, "to"):
                    for port in ports_of(rule):
                        egress.append((filename, policy_name, subject, destination, port))

    problems = []
    checked = 0

    for filename, policy_name, source, destination, port in egress:
        if destination not in protected:
            continue
        checked += 1
        if (destination, source, port) in allowed:
            continue
        if (destination, source, ANY_PORT) in allowed:
            continue
        problems.append(
            "  {}: {} lets {} reach {} on {}, but no ingress rule on the "
            "destination allows it".format(
                filename,
                policy_name,
                dict(source) or "<all pods>",
                dict(destination) or "<all pods>",
                "any port" if port is ANY_PORT else "{}/{}".format(port[0], port[1]),
            )
        )

    print("checked {} in-namespace egress pairs across {}/".format(checked, directory))
    print("  {} pod groups are default-deny for ingress".format(len(protected)))

    if problems:
        print("", file=sys.stderr)
        print("egress with no matching ingress:", file=sys.stderr)
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1

    print("every egress pair has its ingress half")
    return 0


if __name__ == "__main__":
    sys.exit(main())
