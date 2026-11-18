# Sealed secrets

Encrypted with the cluster's Sealed Secrets key, safe to commit. Synced by the
`secrets` Argo CD app (`k8s/argocd/apps/secrets.yaml`).

| File | Secret | Namespace | Created by |
|---|---|---|---|
| `grafana-admin.yaml` | Grafana admin login | `monitoring` | `make doks-seal-grafana` |
| `cloudflare-api-token.yaml` | Cloudflare token for external-dns | `external-dns` | `make doks-seal-cloudflare` |

A new cluster generates a new key and could not decrypt these, so `make doks-down`
backs up the key and `make doks-up` restores it before Argo CD starts. If the backup
is lost, re-run the two `doks-seal-*` targets and commit.
