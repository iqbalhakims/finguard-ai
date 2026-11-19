# Sealed secrets

Encrypted with the cluster's Sealed Secrets key, safe to commit. Synced by the
`secrets` Argo CD app (`k8s/argocd/apps/secrets.yaml`).

| File | Secret | Namespace | Created by |
|---|---|---|---|
| `grafana-admin.yaml` | Grafana admin login | `monitoring` | `make doks-seal-grafana` |
| `cloudflare-api-token.yaml` | Cloudflare token for external-dns | `external-dns` | `make doks-seal-cloudflare` |
| `otel-ingest-token.yaml` | Bearer token for `https://otel.iqbalhakim.site` (OTLP ingest) | `observability` | `make doks-seal-otel` |
| `hubble-ui-auth.yaml` | htpasswd (user `admin`, bcrypt) for `https://hubble.iqbalhakim.site` | `monitoring` | `make doks-seal-hubble` |

A new cluster generates a new key and could not decrypt these, so `make doks-down`
backs up the key and `make doks-up` restores it before Argo CD starts. If the backup
is lost, re-run the `doks-seal-*` targets and commit.
