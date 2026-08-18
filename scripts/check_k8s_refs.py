#!/usr/bin/env python3
"""Cross-reference every name k8s/ points at against something that defines it.

`kubectl apply --dry-run=server` validates each object in isolation: shape,
enums, apiVersions. It cannot see that a Deployment names a ServiceAccount
nothing creates, because a dangling reference is perfectly valid YAML. That is
exactly how `a-game-create-lang-db` sat in a Job while the only ServiceAccount
in the repo was `a-game-create-lang-db-sa` - the Job applied cleanly and then
failed at run time.

Secrets are the deliberate exception. They are created out of band by whoever
installs the app - see the README - so they are checked against EXTERNAL_SECRETS
below rather than against the manifests. Adding a Secret means adding it in both
places, which is the point.

    python3 scripts/check_k8s_refs.py [directory]

Exit 0 if every reference resolves, 1 otherwise.
"""

import sys
from pathlib import Path

import yaml

# Created by hand before `kubectl apply -f k8s/`. Keep in step with the README.
EXTERNAL_SECRETS = {
    "anthropic-credentials",
    "clickhouse-credentials",
    "db-credentials",
    "lang-db-credentials",
    "langfuse-credentials",
    "langfuse-web-secrets",
    "minio-credentials",
    "openai-credentials",
    "rabbitmq-credentials",
    "sports-api-credentials",
}

TRACKED = ("ServiceAccount", "ConfigMap", "PersistentVolumeClaim")


def documents(directory: Path):
    for path in sorted(directory.glob("*.yaml")):
        for doc in yaml.safe_load_all(path.read_text()):
            if isinstance(doc, dict) and doc.get("kind"):
                yield path.name, doc


def walk(node, found, path=()):
    """Every (key path, value) pair anywhere in the tree."""
    if isinstance(node, dict):
        for key, value in node.items():
            found.append((path + (key,), value))
            walk(value, found, path + (key,))
    elif isinstance(node, list):
        for item in node:
            walk(item, found, path)


def collect(directory: Path):
    defined = {kind: set() for kind in TRACKED}
    references = []

    for filename, doc in documents(directory):
        kind = doc["kind"]
        name = (doc.get("metadata") or {}).get("name")
        if kind in defined and name:
            defined[kind].add(name)

        # A StatefulSet mints its own PVCs; pods reference them by template name.
        for template in (doc.get("spec") or {}).get("volumeClaimTemplates") or []:
            template_name = (template.get("metadata") or {}).get("name")
            if template_name:
                defined["PersistentVolumeClaim"].add(template_name)

        pairs = []
        walk(doc, pairs)
        for keys, value in pairs:
            if not isinstance(value, str):
                continue
            key = keys[-1]
            parent = keys[-2] if len(keys) > 1 else ""

            if key == "serviceAccountName":
                references.append((filename, name, "ServiceAccount", value))
            elif key == "claimName":
                references.append((filename, name, "PersistentVolumeClaim", value))
            elif key == "secretName":
                references.append((filename, name, "Secret", value))
            elif key == "name" and parent in ("configMapKeyRef", "configMapRef", "configMap"):
                references.append((filename, name, "ConfigMap", value))
            elif key == "name" and parent in ("secretKeyRef", "secretRef"):
                references.append((filename, name, "Secret", value))

    return defined, references


def main() -> int:
    directory = Path(sys.argv[1] if len(sys.argv) > 1 else "k8s")
    if not directory.is_dir():
        print("not a directory: {}".format(directory), file=sys.stderr)
        return 2

    defined, references = collect(directory)
    problems = []

    for filename, owner, kind, target in sorted(set(references)):
        known = EXTERNAL_SECRETS if kind == "Secret" else defined[kind]
        if target in known:
            continue
        where = "EXTERNAL_SECRETS" if kind == "Secret" else "any {} in {}/".format(kind, directory)
        problems.append(
            "  {}: {} references {} '{}', which is not in {}".format(
                filename, owner or "<unnamed>", kind, target, where
            )
        )

    print("checked {} references across {}/".format(len(set(references)), directory))
    for kind in TRACKED:
        print("  {:3d} {}s defined".format(len(defined[kind]), kind))

    if problems:
        print("", file=sys.stderr)
        print("dangling references:", file=sys.stderr)
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1

    print("all references resolve")
    return 0


if __name__ == "__main__":
    sys.exit(main())
