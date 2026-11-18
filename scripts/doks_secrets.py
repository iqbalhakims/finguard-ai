"""Carry cluster-generated secrets across DOKS rebuilds.

A rebuilt cluster would otherwise:
  * generate a new Sealed Secrets key -> SealedSecrets already in git can't be decrypted
  * request a new Let's Encrypt certificate -> hits the 5-per-week duplicate limit

    python scripts/doks_secrets.py backup  --context do-sgp1-finguard   # before destroying
    python scripts/doks_secrets.py restore --context do-sgp1-finguard   # right after creating,
                                                                         # before Argo CD syncs

Backups are written to k8s/.secrets/ (gitignored, mode 0600). They contain private
keys: keep a copy in your password manager too.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ITEMS = [
    {
        "file": "sealed-secrets-keys.json",
        "namespace": "kube-system",
        "get": ["get", "secret", "-l", "sealedsecrets.bitnami.com/sealed-secrets-key", "-o", "json"],
        "optional": False,
    },
    {
        "file": "public-gateway-tls.json",
        "namespace": "monitoring",
        "get": ["get", "secret", "public-gateway-tls", "-o", "json"],
        "optional": True,  # absent until the first certificate has been issued
    },
]
SERVER_FIELDS = ("uid", "resourceVersion", "creationTimestamp", "managedFields", "selfLink", "generation")


def kubectl(context: str, args: list[str], stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["kubectl", "--context", context, *args], input=stdin, capture_output=True, text=True
    )


def clean(obj: dict) -> dict:
    meta = obj.get("metadata", {})
    for field in SERVER_FIELDS:
        meta.pop(field, None)
    meta.get("annotations", {}).pop("kubectl.kubernetes.io/last-applied-configuration", None)
    return obj


def backup(context: str, directory: Path) -> int:
    directory.mkdir(parents=True, exist_ok=True)
    for item in ITEMS:
        res = kubectl(context, ["-n", item["namespace"], *item["get"]])
        if res.returncode != 0:
            if item["optional"]:
                print(f"skip   {item['file']}: {res.stderr.strip().splitlines()[-1]}")
                continue
            print(f"ERROR  {item['file']}: {res.stderr.strip()}", file=sys.stderr)
            return 1
        data = json.loads(res.stdout)
        objects = data["items"] if data.get("kind") == "List" else [data]
        if not objects:
            print(f"ERROR  {item['file']}: nothing found", file=sys.stderr)
            return 1
        out = {"apiVersion": "v1", "kind": "List", "items": [clean(o) for o in objects]}
        path = directory / item["file"]
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(out, fh, indent=2)
        print(f"saved  {path} ({len(objects)} secret(s))")
    return 0


def restore(context: str, directory: Path) -> int:
    for item in ITEMS:
        path = directory / item["file"]
        if not path.exists():
            print(f"skip   {path}: no backup")
            continue
        ns = item["namespace"]
        if kubectl(context, ["get", "namespace", ns]).returncode != 0:
            res = kubectl(context, ["create", "namespace", ns])
            if res.returncode != 0:
                print(f"ERROR  namespace {ns}: {res.stderr.strip()}", file=sys.stderr)
                return 1
        res = kubectl(context, ["-n", ns, "apply", "-f", "-"], stdin=path.read_text())
        if res.returncode != 0:
            print(f"ERROR  {path}: {res.stderr.strip()}", file=sys.stderr)
            return 1
        print(f"restored {path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=["backup", "restore"])
    parser.add_argument("--context", required=True, help="kubeconfig context (never the current one implicitly)")
    parser.add_argument("--dir", type=Path, default=Path("k8s/.secrets"))
    args = parser.parse_args()
    return backup(args.context, args.dir) if args.action == "backup" else restore(args.context, args.dir)


if __name__ == "__main__":
    sys.exit(main())
