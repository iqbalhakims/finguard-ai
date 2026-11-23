# FinGuard AI — Banking Fraud Detection Platform

A miniature but complete ML platform for real-time card-fraud detection. It covers
data processing, model training, evaluation, explainability, experiment tracking,
serving, containerisation, Kubernetes, monitoring and drift detection.

```
 ┌──────────────── offline (python -m …) ────────────────┐        ┌──────────── online ────────────────────────────┐
 │ data.generate ─► data.process ─► train ─────────────► │ bundle │ FastAPI  /predict ─► XGBoost ─► approve/review/│
 │ synthetic txns   pandas:        LR vs XGBoost         │ ─────► │          ?explain ─► SHAP TreeExplainer  block │
 │ + fraud patterns validation,    PR-AUC, thresholds    │ (image)│          drift monitor (PSI, rolling window)   │
 │                  point-in-time  SHAP plots            │        │          /metrics ─► Prometheus ─► Grafana      │
 │                  aggregates     MLflow runs+registry  │        │                          └─► alert rules        │
 │ drift  ◄── drifted data: PSI + KS + perf decay ──► MLflow       │ Docker image ─► docker compose / Kubernetes     │
 └───────────────────────────────────────────────────────┘        └─────────────────────────────────────────────────┘
```

## Results (synthetic data, time-based split, held-out test set)

| Model | PR-AUC | Precision | Recall | F1 | Precision @ 80% recall | Fraud € caught |
|---|---|---|---|---|---|---|
| Logistic Regression | 0.678 | 0.801 | 0.477 | 0.598 | 0.337 | 52% |
| **XGBoost (champion)** | **0.846** | **0.937** | **0.647** | **0.766** | **0.692** | **68%** |

Base fraud rate is ~0.7%, so a random model scores PR-AUC ≈ 0.007. Thresholds are
picked on the validation window. Scores of 0.514 and above go to an analyst
**review** queue. Scores of 0.653 and above, where validation precision is at least
95%, are **blocked** in real time. Recall tops out at around 65% partly because of
deliberately undetectable "friendly fraud" (disputed purchases that look normal
when the card is authorised).

**Drift scenario** (new mobile app + premium card launch): 7 of 11 monitored
features drift, drift is detected, and on the shifted data PR-AUC falls 0.846 → 0.708
and precision falls 0.94 → 0.55. That is the signal to retrain.

## Feature map

| # | Requirement | Where |
|---|---|---|
| 1 | Transaction processing with Python & Pandas | [finguard/data/generate.py](finguard/data/generate.py), [finguard/data/process.py](finguard/data/process.py), [finguard/features.py](finguard/features.py) |
| 2 | Logistic Regression & XGBoost | [finguard/train.py](finguard/train.py) |
| 3 | Precision, recall, PR-AUC | [finguard/evaluate.py](finguard/evaluate.py) |
| 4 | SHAP explainability | [finguard/explain.py](finguard/explain.py) (global plots in MLflow, per-request reasons in the API) |
| 5 | MLflow tracking + registry | [finguard/train.py](finguard/train.py), [finguard/drift.py](finguard/drift.py) |
| 6 | FastAPI inference service | [finguard/api/](finguard/api/) |
| 7 | Docker | [Dockerfile](Dockerfile), [docker-compose.yml](docker-compose.yml) |
| 8 | Kubernetes | [k8s/](k8s/): Helm charts, Argo CD apps (GitOps) |
| 9 | Prometheus & Grafana | [finguard/api/metrics.py](finguard/api/metrics.py), [k8s/charts/finguard/files/](k8s/charts/finguard/files/), kube-prometheus-stack on DOKS |
| 10 | Data drift detection | [finguard/drift.py](finguard/drift.py), [finguard/api/monitor.py](finguard/api/monitor.py) |

## Quickstart

Requirements: Python 3.11+, Docker. For Kubernetes you also need minikube and kubectl.

```bash
make setup        # .venv + dependencies
make pipeline     # data → train (MLflow) → drifted data → drift report → tests
make up           # Docker: API + traffic simulator + Prometheus + Grafana + MLflow
```

| Service | URL |
|---|---|
| API docs (Swagger) | http://localhost:8000/docs |
| Grafana | http://localhost:3300 (anonymous view; `admin`/`admin` to edit) |
| Prometheus | http://localhost:9095 (Alerts tab shows the rules) |
| MLflow server (compose) | http://localhost:5001 |
| MLflow UI for local runs | `make mlflow-ui` → http://localhost:5002 |

Host ports can be changed with `API_PORT`, `GRAFANA_PORT`, `PROMETHEUS_PORT` and `MLFLOW_PORT`.

**Drift demo:** run `make compose-drift`. Within about two minutes the Grafana
"Data drift" row turns red, the flag rate climbs, and `FinGuardDataDrift` fires
in Prometheus after 5 minutes. Run `make compose-baseline` to recover.

## 1 · Data

Real card data is confidential, so [generate.py](finguard/data/generate.py) simulates
4,000 customers over 120 days (~300k transactions, ~0.7% fraud). Each customer has
their own typical spend, active hours, channel mix and travel habits. There are five
fraud typologies, each with its own signature:

| Type | Signature |
|---|---|
| `cnp` | online, new device, high value, often at night and far away (half routed through nearby proxies) |
| `account_takeover` | bursts of 4–10 online purchases minutes apart |
| `card_testing` | tiny probe charges, then one large purchase |
| `skimming` | cloned card used in person far from home, heavy ATM use |
| `friendly_fraud` | disputed purchase that looks completely normal (caps achievable recall) |

The legitimate data deliberately overlaps with fraud: travellers, night owls,
shopping sprees, big-ticket purchases, new phones, and VPN/mobile IP geolocation noise.

[process.py](finguard/data/process.py) validates the schema and drops duplicates,
nulls, non-positive amounts and unknown categories, recording a data-quality report.
It then derives **point-in-time customer aggregates** using `groupby().rolling(..., closed="left")`:
`txn_count_1h`, `txn_count_24h`, `amount_sum_24h`, `avg_amount_30d` and
`seconds_since_last_txn`. Only transactions strictly before the current one are
used, so there is no look-ahead leakage; this is covered by
[tests/test_process.py](tests/test_process.py).

[features.py](finguard/features.py) builds the 30 model features. These are log amounts, amount
relative to the customer's 30-day average, velocity, cyclic hour, night/weekend
flags, and one-hot channel and category from **fixed vocabularies**. Training and the
API call the same function, and the model bundle refuses to load if its feature
list differs from the code. This removes train/serve skew.

## 2–3 · Models and evaluation

- **Split by time**: 70% train / 15% validation / 15% test, because a deployed model always scores the future.
- **Logistic Regression** (standardised) as the transparent baseline. **XGBoost** (hist, early stopping on validation PR-AUC) as the challenger.
- Class imbalance is handled with **threshold tuning, not re-weighting**, so the scores stay meaningful as probabilities.
- Metrics are chosen for a ~1% base rate: **PR-AUC** (headline), precision, recall and F1 at the operating threshold, precision at 80% recall (how noisy the alert queue gets), and **fraud amount recall** (share of fraudulent money caught). ROC-AUC is logged for reference only, because it flatters imbalanced problems (0.985 here).
- The champion is the model with the best **validation** PR-AUC. Test metrics are reported but never used to make choices.

## 4 · Explainability (SHAP)

- **Training**: for each model, a SHAP beeswarm plot, a mean-|SHAP| bar chart and a JSON of global importances are logged to MLflow. The top drivers are distance from home, amount vs. the customer's usual spend, new device, online channel, and time since the last transaction.
- **Serving**: `POST /predict?explain=true` returns the top 5 feature contributions in log-odds (`TreeExplainer` for XGBoost, `LinearExplainer` for LR). That is a reason code an analyst or a customer-facing team can act on. A test checks that the SHAP values plus the base value reconstruct the model's output exactly.

## 5 · MLflow

`make train` creates a parent run with one nested run per model. It logs:

- params and dataset lineage (`mlflow.log_input`)
- validation and test metrics
- PR curve, confusion matrix and SHAP plots
- the model with its signature and input example
- a comparison table
- the exported serving bundle

The champion is registered as **`finguard-fraud-detector`** with alias **`@champion`**,
and tagged with its metrics and threshold. Drift checks are logged to a separate
`finguard-drift` experiment.

Runs are stored locally in `sqlite:///mlflow.db` by default. To log to the compose
tracking server instead (artifacts are proxied, nothing to configure):

```bash
MLFLOW_TRACKING_URI=http://localhost:5001 make train
```

## 6 · Inference API

```bash
curl -s -X POST 'localhost:8000/predict?explain=true' -H 'content-type: application/json' -d '{
  "transaction_id": "txn_demo_0001", "customer_id": "cust_00042",
  "timestamp": "2026-11-14T02:13:00+08:00", "amount": 1249.99,
  "merchant_category": "electronics", "channel": "online",
  "card_present": false, "is_international": true, "new_device": true,
  "distance_from_home_km": 6800,
  "txn_count_1h": 3, "txn_count_24h": 4, "amount_sum_24h": 310.5,
  "avg_amount_30d": 58.2, "seconds_since_last_txn": 420 }'
```

```json
{ "fraud_probability": 0.976867, "decision": "block", "is_fraud": true,
  "thresholds": {"review": 0.5143, "block": 0.6526},
  "model_name": "finguard-fraud-detector", "model_version": "2",
  "explanation": { "method": "shap", "base_value": -5.15, "contributions": [
    {"feature": "new_device", "value": 1.0, "shap_value": 2.78, "effect": "increases_risk"},
    {"feature": "distance_from_home_log", "value": 8.82, "shap_value": 1.42, "effect": "increases_risk"}, … ] } }
```

| Endpoint | Purpose |
|---|---|
| `POST /predict` | Score one transaction (`?explain=true` for SHAP reasons) |
| `POST /predict/batch` | Score up to 1,000 transactions |
| `GET /model` | Model version, thresholds, offline metrics, MLflow lineage |
| `GET /drift` | Latest drift report for live traffic (`?refresh=true` to recompute) |
| `POST /drift/reset` | Clear the drift window after a planned change |
| `GET /metrics` | Prometheus exposition |
| `GET /health/live`, `/health/ready` | Probes (ready = model loaded) |

The behavioural fields (`txn_count_*`, `amount_sum_24h`, `avg_amount_30d`,
`seconds_since_last_txn`) are what a feature store (Feast/Redis, for example) would
return at authorisation time. They are optional, and a first-time customer simply omits them.

## 7 · Docker

The [Dockerfile](Dockerfile) is a multi-stage build on `python:3.12-slim`:

- Lean runtime dependencies ([requirements-serve.txt](requirements-serve.txt)): no MLflow, no matplotlib, and the CPU-only XGBoost wheel on Linux.
- Runs as non-root UID 10001, with a `HEALTHCHECK` and a single Uvicorn worker per container.
- The model bundle is **baked into the image**, so each image tag is one immutable, auditable model version.

The [docker-compose.yml](docker-compose.yml) stack runs the API, the traffic
simulator, Prometheus (with alert rules), Grafana (dashboard provisioned) and an
MLflow tracking server.

## 8 · Kubernetes (Helm + GitOps)

Everything Kubernetes lives in [k8s/](k8s/):

```
k8s/
  charts/finguard/        Helm chart: API, simulator, and either a bundled Prometheus + Grafana
                          (local) or ServiceMonitor + PrometheusRule + dashboard ConfigMap
                          for kube-prometheus-stack (DOKS)
  charts/public-gateway/  Helm chart: Gateway API entry point + Let's Encrypt certificate
  charts/hubble-auth/     Helm chart: password (basic auth) proxy in front of DOKS's Hubble UI
  charts/wazuh/           Helm chart: Wazuh SIEM (indexer, manager, dashboard, node agents)
  values/                 doks.yaml, kube-prometheus-stack.yaml, public-gateway.yaml, minikube.yaml
  argocd/                 root.yaml (app of apps) + apps/: cert-manager, sealed-secrets,
                          kube-prometheus-stack, finguard, public-gateway
  bootstrap/              Argo CD install values (the only thing installed by hand)
```

**Locally (minikube)** the chart is installed directly with Helm, using the
bundled Prometheus and Grafana:

```bash
make minikube-start   # dedicated profile "finguard", started with --keep-context
make k8s-load         # build the image and load it into minikube
make k8s-deploy       # helm upgrade --install finguard k8s/charts/finguard -f k8s/values/minikube.yaml
make k8s-forward      # API :8080 · Grafana :3300 · Prometheus :9095
make k8s-drift        # helm upgrade with simulator.drift=true
```

All `k8s-*` and `doks-*` targets pass an explicit `--context`/`--kube-context`,
so they never touch whatever cluster your kubeconfig currently points at.

What the chart deploys (namespace `finguard`):

- The namespace enforces the **restricted** Pod Security Standard. Every pod runs as non-root with a read-only root filesystem (Grafana aside), all capabilities dropped, and a seccomp profile.
- The API runs **2 replicas** with rolling updates (`maxUnavailable: 0`), startup, readiness and liveness probes, a `preStop` drain, topology spread and a **PDB** (`minAvailable: 1`). An **HPA** (2→6 at 70% CPU) is optional: on for minikube, off on DOKS.
- A traffic simulator keeps the dashboards alive (`simulator.drift=true` for the drift demo).
- Monitoring config comes from [k8s/charts/finguard/files/](k8s/charts/finguard/files/): alert rules, dashboard and Grafana provisioning, the **same files docker-compose mounts**. Pods roll automatically when they change (checksum annotations).
- **Bundled mode** (local): an in-namespace Prometheus discovers annotated pods (namespaced Role only) and scrapes each pod directly, and Grafana is provisioned from ConfigMaps.
- **kube-prometheus-stack mode** (DOKS): a `ServiceMonitor`, a `PrometheusRule` (the same 6 alerts) and a dashboard ConfigMap that the Grafana sidecar picks up.

## Infrastructure: DOKS with Terraform (HCP Terraform)

[terraform/](terraform/) creates one DigitalOcean Kubernetes cluster. State and runs are in HCP Terraform (organization `iqbal-hakim`, workspace `finguard-ai`).

| File | Contents |
|---|---|
| [main.tf](terraform/main.tf) | One DOKS cluster in **Singapore (`sgp1`)** on the latest 1.35 patch: **1 × `s-2vcpu-4gb-amd`** node, standard (non-HA) control plane, and pulls from the `finai` registry without image pull secrets. Plus a **5 GiB block storage volume** for Prometheus |
| [variables.tf](terraform/variables.tf) | Region, cluster name, Kubernetes version, node size and count, volume size |
| [outputs.tf](terraform/outputs.tf) | Cluster ID, API endpoint, kube context name, `doctl` kubeconfig command, Prometheus volume ID |

**One-time setup**

```bash
cd terraform
terraform login      # HCP Terraform API token
terraform init       # creates workspace "finguard-ai" in org "iqbal-hakim" if it doesn't exist
```

In HCP, go to Workspace → **Variables** and add an **environment** variable
`DIGITALOCEAN_TOKEN`, marked sensitive. Use a DO API token with write access to
Kubernetes and Block Storage, plus read access to the registry.

**Applying changes.** The workspace is connected to GitHub (VCS-driven) with
**auto-apply on**, and its Terraform working directory is `terraform`. Every push
to `main` queues a run that applies whatever is in `terraform/`, so a push *is* the
apply. From the CLI you can only preview:

```bash
terraform plan       # speculative plan, runs remotely in HCP Terraform
# terraform apply / destroy are refused for VCS-connected workspaces
doctl kubernetes cluster kubeconfig save finguard --set-current-context=false   # adds context do-sgp1-finguard
```

`--set-current-context=false` leaves your current kubectl context untouched.
The `doks-*` and `argocd-*` targets use that context by default (`DOKS_CONTEXT`).

> **Tearing down.** For the daily stop, don't queue a destroy run: it also deletes the
> Prometheus volume and its history. Use `make doks-down` and then
> `cluster_enabled = false`, as described below. A full destroy run (HCP → Workspace →
> Settings → Destruction and Deletion) is only for removing everything. Run
> `make doks-down` first there too, or the Gateway's load balancer is left behind and
> keeps billing. If you ever delete the cluster from the DigitalOcean UI, **don't tick
> "destroy associated resources"**: the storage driver tags the Prometheus volume as
> associated with the cluster, so its metrics history would be deleted too.

### Deploying to DOKS with GitOps (Argo CD)

Argo CD is installed with Helm whenever the cluster is created. After that,
**everything is deployed from git**: change something under `k8s/`, commit, push,
and Argo CD syncs it (automated sync with prune and self-heal).

| Argo CD app | What | Namespace |
|---|---|---|
| `cert-manager` | Official chart v1.21.3, Gateway API support on | `cert-manager` |
| `sealed-secrets` | Official chart 2.22.0 (controller 0.41.2) | `kube-system` |
| `external-dns` | Official chart 1.23.0: keeps the public hosts (`grafana`, `argo`, `otel`, `hubble` `.iqbalhakim.site`) in **Cloudflare** pointed at the current Gateway IP. Only HTTPRoute hosts under `iqbalhakim.site`, **upsert-only** (never deletes), TXT ownership records (only touches records it created), DNS only (not proxied), TTL 60s | `external-dns` |
| `kube-prometheus-stack` | Official chart 92.17.0 with [values](k8s/values/kube-prometheus-stack.yaml): Prometheus on the **Terraform volume** (15 days, max 4 GB), Grafana (login required), Alertmanager, kube-state-metrics, node-exporter, and Hubble's network metrics (drops, L7 HTTP, DNS) from DOKS's Cilium. Picks up ServiceMonitors, rules and dashboards from every namespace | `monitoring` |
| `finguard` | [This chart](k8s/charts/finguard/) with [doks.yaml](k8s/values/doks.yaml): registry image `sha-<commit>`, 2 API replicas, simulator, ServiceMonitor, PrometheusRule, dashboards (overview, and **Outage**: failures the API cannot see, from Hubble + traces) | `finguard` |
| `public-gateway` | [Gateway chart](k8s/charts/public-gateway/): **Cilium Gateway API** (built into DOKS) → Grafana, the Argo CD UI, OTLP ingest and the Hubble UI (one load balancer, one certificate), HTTP→HTTPS redirect, HSTS, **Let's Encrypt** via cert-manager HTTP-01 | `monitoring` |
| `secrets` | The SealedSecrets in [k8s/secrets/](k8s/secrets/): Grafana login, Cloudflare token | per file |
| `otel-collector` | Official chart 0.178.2: OTLP traces in (in-cluster without a token, `otel.iqbalhakim.site` with a bearer token) → Tempo | `observability` |
| `tempo` | [Tempo chart](k8s/charts/tempo/): trace storage (72 h), Grafana datasource | `observability` |
| `loki` | [Loki chart](k8s/charts/loki/): log storage (7 days) on the **Terraform volume** `finguard-loki`, Grafana datasource | `observability` |
| `otel-agent` | Official collector chart 0.178.2 as a **DaemonSet**: reads every pod's logs on its node, adds Kubernetes attributes, sends them to Loki (OTLP) | `observability` |
| `falco` | Official chart 9.2.2 (Falco 0.45): **runtime threat detection**. DaemonSet with the modern eBPF probe (no kernel module); default rules + [FinGuard rules](k8s/values/falco.yaml); events → Loki, rule counters → Prometheus, official Falco dashboard | `falco` |
| `hubble-auth` | [Hubble auth chart](k8s/charts/hubble-auth/): nginx with **basic auth** in front of the Hubble UI that DOKS runs in `kube-system` (Hubble UI has no login of its own) | `monitoring` |
| `wazuh` | [Wazuh chart](k8s/charts/wazuh/) 4.14.10 (SIEM): indexer (OpenSearch), manager, dashboard, and an **agent DaemonSet** watching every node (file integrity of `/etc` and binaries, rootkit checks, processes, ports). Single node, **starts empty after each rebuild**, no LoadBalancer Services. Also ships the Grafana **SOC** dashboard (alerts by severity, host, rule, MITRE ATT&CK, PCI DSS; file-integrity events), read through a read-only indexer user | `wazuh` |

Public: Grafana at `https://grafana.iqbalhakim.site` and the Argo CD UI at
`https://argo.iqbalhakim.site` (login `admin`, password in
`k8s/.secrets/argocd-admin-password`, set by `make argocd-bootstrap` and kept across
rebuilds). The Hubble UI (Cilium network flows) is at `https://hubble.iqbalhakim.site`
(login `admin`, password in `k8s/.secrets/hubble-ui-password`, sealed by
`make doks-seal-hubble`). Wazuh is at `https://wazuh.iqbalhakim.site` (login `admin`,
password in `k8s/.secrets/wazuh/indexer-admin-password`, from `make doks-seal-wazuh`); its
alerts are also on Grafana's "FinGuard AI — SOC" dashboard.
More hosts go under `extraRoutes` in
[k8s/values/public-gateway.yaml](k8s/values/public-gateway.yaml). The API and
Prometheus stay internal (ClusterIP).

**One-time setup** (with the cluster running):

```bash
brew install kubeseal
make doks-up                     # kubeconfig + Argo CD (first time: nothing to restore yet)
make doks-seal-grafana           # random Grafana login  -> k8s/secrets/grafana-admin.yaml
make doks-seal-cloudflare        # Cloudflare API token  -> k8s/secrets/cloudflare-api-token.yaml
make doks-seal-hubble            # random Hubble UI login -> k8s/secrets/hubble-ui-auth.yaml
make doks-seal-wazuh             # Wazuh certs + passwords -> k8s/secrets/wazuh-*.yaml, grafana-wazuh.yaml
git add k8s/secrets && git commit -m "add sealed secrets" && git push
```

The Cloudflare token comes from **Cloudflare → My Profile → API Tokens → Create
Token → "Edit zone DNS"** template, with Zone Resources limited to **`iqbalhakim.site`
only**. `make doks-seal-cloudflare` prompts for it without echoing, and only the
encrypted file is written. Never paste the token anywhere else.

**Daily: the cluster only runs a few hours a day.** The domain stays the same and
external-dns moves it to each new load balancer IP.

| | Steps |
|---|---|
| **Start** | HCP Terraform → workspace `finguard-ai` → variable `cluster_enabled = true` → start a run, then `make doks-up` |
| **Stop** | `make doks-down` (backs up keys + certificate, removes the load balancer), then set `cluster_enabled = false` and start a run |

`make doks-down` deletes the Gateway first, so DigitalOcean removes its load balancer.
Otherwise the load balancer would outlive the cluster and keep billing.
`cluster_enabled = false` then removes the cluster. The **Prometheus volume is kept**,
so metrics history carries over. Each start then:

- restores the **Sealed Secrets keys**, so the secrets in git still decrypt. A new cluster would otherwise generate a new key.
- restores the **Let's Encrypt certificate**, so it isn't re-issued daily. Let's Encrypt allows only 5 duplicate certificates per week.
- bootstraps Argo CD, which deploys everything from git; external-dns then updates the DNS record within about 2 minutes.

Backups live in `k8s/.secrets/` (gitignored). They contain private keys, so keep a
copy in your password manager. If they're lost, re-run the two `doks-seal-*` targets
and commit.

```bash
make argocd-status               # sync / health of all apps
make doks-cert-status            # gateway, routes, certificate
make argocd-ui                   # Argo CD UI via port-forward on http://localhost:8443
make doks-forward                # internal: API :8080 · Grafana :3300 · Prometheus :9095
```

To log in to Grafana, use `admin` and the password in
`k8s/.secrets/grafana-admin-password`. To deploy a new image, set `image.tag` in
[k8s/values/doks.yaml](k8s/values/doks.yaml) to a CI tag (`sha-<commit>`) and push.

Terraform-only commits don't trigger the image pipeline, because `terraform/**` is in its `paths-ignore`.

## CI/CD → DigitalOcean Container Registry

[.github/workflows/ci.yml](.github/workflows/ci.yml) runs on every push and pull request to `main`, and on `v*` tags.

| Job | Steps |
|---|---|
| **Test, train & quality gate** | unit tests → generate data → train both models (MLflow-tracked) → **quality gate** ([scripts/check_model.py](scripts/check_model.py)) → drift report smoke test |
| **Build, scan & push** | build `linux/amd64` → **Trivy** scan → push to `registry.digitalocean.com/finai/finguard-api` |

- **Quality gate:** the pipeline fails unless the champion's test metrics reach PR-AUC ≥ 0.75, precision ≥ 0.80 and recall ≥ 0.50. A model that has regressed never gets packaged into an image.
- **Trivy:** the scan fails the job on fixable CRITICAL or HIGH vulnerabilities. The image is pushed only after the scan passes, and never from pull requests.
- **Tags:** `sha-<short>` on every push, `latest` on `main`, and `X.Y.Z` on `vX.Y.Z` tags.
- **Image labels:** each image records the model type and test PR-AUC.
- **Run artifacts:** each run uploads the serving bundle and the MLflow run (metrics, PR curves, SHAP plots) as artifacts you can download.
- **Pinned actions:** all actions are pinned to commit SHAs, and [Dependabot](.github/dependabot.yml) updates actions, pip and Docker base images weekly.

**One-time setup:** add a repository secret `DIGITALOCEAN_ACCESS_TOKEN`, a DO API token with read/write access to the `finai` registry. To have a DOKS cluster pull from the registry later, run `doctl kubernetes cluster registry add <cluster>`.

> CI trains with a fresh MLflow SQLite file on each run, so the registry version in
> the image is always `1`, and the real lineage is the git SHA tag. A shared MLflow
> server would give versions that persist across runs.

## 9 · Monitoring

Metrics exposed by the API ([metrics.py](finguard/api/metrics.py)):

| Metric | Type | Meaning |
|---|---|---|
| `finguard_http_requests_total{route,status}` | counter | traffic and errors |
| `finguard_http_request_duration_seconds` | histogram | end-to-end latency |
| `finguard_inference_duration_seconds{stage}` | histogram | model vs. SHAP time |
| `finguard_predictions_total{decision,model_version}` | counter | approve / review / block |
| `finguard_fraud_score` | histogram | model output distribution |
| `finguard_scored_amount_total{decision}` | counter | money through each decision |
| `finguard_model_info{model_version,model_type}` | gauge | which model is live |
| `finguard_feature_drift_psi{feature}`, `finguard_prediction_drift_psi` | gauge | drift per feature and for the score |
| `finguard_drift_detected`, `finguard_drift_share` | gauge | drift verdict |

The Grafana dashboard **"FinGuard AI — Fraud Platform"** has four parts:

- A headline row: throughput, p95 latency, error rate, flag rate, drift status, live model version, healthy replicas.
- Service health: requests, latency percentiles, inference time by stage.
- Fraud decisions: decisions/s, flag and block rate, score percentiles, value scored per decision.
- Data drift: a per-feature PSI status history, current PSI by feature, prediction PSI, share of features drifted.

Alert rules ([alerts.yml](k8s/charts/finguard/files/prometheus/alerts.yml)):

| Alert | Fires when |
|---|---|
| `FinGuardApiDown` | a scrape target is down |
| `FinGuardHighErrorRate` | 5xx rate is above 2% |
| `FinGuardHighLatencyP95` | p95 latency is above 100 ms (card-authorisation SLA) |
| `FinGuardFraudFlagRateSpike` | more than 5% of transactions are flagged |
| `FinGuardDataDrift` | the drift verdict is positive |
| `FinGuardPredictionDrift` | score PSI is 0.25 or higher |

### Traces (OpenTelemetry)

The API emits **OpenTelemetry traces** when `OTEL_EXPORTER_OTLP_ENDPOINT` is set
([telemetry.py](finguard/api/telemetry.py)); without it, tracing is off.

- **Spans:** one server span per request (FastAPI auto-instrumentation), with child spans `features.build`, `model.predict`, `shap.explain` and `drift.observe`. Spans carry the model version, batch size, decision and fraud probability.
- **Logs:** every log line includes `trace_id` and `span_id`.
- **Exemplars:** the request-latency histogram carries `trace_id` exemplars. In Grafana, the dots on a latency panel open the trace behind that request.

On DOKS:

| Component | What | Where |
|---|---|---|
| `otel-collector` | Official chart 0.178.2 (collector-contrib 0.163.2). OTLP in, adds Kubernetes attributes, batches, sends to Tempo | `observability` |
| `tempo` | Small [chart](k8s/charts/tempo/) running Tempo 2.10 as a single binary, 72 h retention, temporary storage (traces reset on rebuild) | `observability` |
| `otel-agent` | The same collector as a DaemonSet: every pod's log files → Loki | `observability` |
| `loki` | Small [chart](k8s/charts/loki/) running Loki 3.7 as a single binary, 7 days, on the Terraform volume `finguard-loki` (logs survive the daily rebuild) | `observability` |
| Grafana | Tempo and Loki datasources; Prometheus exemplars link to traces, traces link to their logs, log lines link to their trace | `grafana.iqbalhakim.site` |

**Logs:** the **Logs** row at the bottom of the FinGuard dashboard shows API log lines
by level, error-like lines per namespace across the cluster, and the live API log.
In **Explore → Loki**, e.g. `{service_name="finguard-api"} |= "blocked"` or
`{k8s_namespace_name="kube-system"} |~ "(?i)error"`. From a trace in Tempo,
**Logs for this span** opens its log lines; from a log line, **View trace** opens the trace.

**Service graph:** the traffic simulator plays the card-authorisation system. It
creates a CLIENT span (`card.authorise`) for each call and passes `traceparent` on,
so its calls and the API's work are one trace. Tempo's **metrics-generator** turns
these spans into service-graph and per-span rate/error/duration metrics, written into
Prometheus. Grafana shows them as the **Service graph** panel at the bottom of the
FinGuard dashboard, and in **Explore → Tempo → Service Graph**:
`traffic-simulator → finguard-api` with request rate, error % and latency.

The API samples **10%** of requests (`otel.samplerRatio` in [doks.yaml](k8s/values/doks.yaml)).

**Sending traces from outside the cluster:** `https://otel.iqbalhakim.site` is the
Collector's OTLP/HTTP endpoint. It requires a bearer token, kept in
`k8s/.secrets/otel-ingest-token` (sealed into the cluster by `make doks-seal-otel`):

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT=https://otel.iqbalhakim.site
export OTEL_EXPORTER_OTLP_HEADERS="Authorization=Bearer%20$(cat k8s/.secrets/otel-ingest-token)"   # %20: header values must be URL-encoded
export OTEL_SERVICE_NAME=my-app     # any OTLP/HTTP SDK or collector picks these up
```

In-cluster apps send to `http://otel-collector.observability.svc:4318` without a token;
that port isn't exposed publicly.

### Failure scenarios (Hubble + the Outage dashboard)

When requests never reach the API, its own metrics stay green: error rate 0%, SLO
untouched, traffic just disappears. [k8s/chaos/](k8s/chaos/) holds switchable failures
(Cilium network policies, one client pod) to see this in **Hubble UI**
(`https://hubble.iqbalhakim.site`, namespace `finguard`) and in Grafana's
**FinGuard AI — Outage** dashboard, which puts the network (Hubble metrics), client
(simulator traces) and server (API metrics) views side by side.

```bash
make chaos-list                  # what's available
make chaos S=drop-simulator      # start one (replaces any running scenario)
make chaos-off                   # stop
```

| Scenario | What breaks | Hubble UI shows |
|---|---|---|
| `l7-visibility` | nothing (baseline) | each request: `POST /predict → 200 (40ms)` |
| `drop-simulator` | simulator → API packets dropped | red edge, `Policy denied … DROPPED (TCP SYN)` |
| `http-403` | L7 policy allows only GET | `403 0ms (POST /predict)`: Envoy answers, the app never sees it |
| `dns-outage` | simulator can't reach CoreDNS | `→ coredns:53 DROPPED (UDP)`* |
| `cut-traces` | app + simulator can't reach the OTel Collector | drops to `observability/otel-collector`; predictions keep working |
| `nxdomain` | a client calls `finguard-apii` | DNS `Non-Existent Domain` answers* |
| `intruder` | nothing: a compromised pod hunts for SSH keys and tries a reverse shell | little; **Falco** catches it (see below) |

\* Hubble UI hides kube-dns traffic by default: untick **Visual → Hide kube-dns:53 pod**.
Scenarios are applied with kubectl, not Argo CD, so self-heal leaves them alone.

### Runtime security (Falco)

Network policies and image scanning (Trivy in CI) can't see what a running container
*does*. Falco watches every process, file and network syscall on the node through
eBPF and matches them against rules: the default set (a shell spawned in a container,
searching for private keys, netcat reverse shells, running a dropped binary, ...) plus a
FinGuard allowlist in [k8s/values/falco.yaml](k8s/values/falco.yaml): only Python (the
API and the simulator) may run in the `finguard` namespace, so any other process there,
including a `kubectl exec` debug shell, is reported.

```bash
make chaos S=intruder            # a fake compromised pod: three rules fire every 30 s
make chaos-off
```

Where to look: the **Security · Falco** row at the bottom of the FinGuard dashboard
(alerts by rule, event details), the official **Falco** dashboard (Falco's own health),
and **Explore → Loki**: `{service_name="falco"} | json | priority="Warning"`.
Falco only detects; it does not block or kill (that would be Tetragon or Falco Talon).

## 10 · Drift detection

- **PSI per monitored feature** against a reference profile saved in the model bundle at training time. Numeric features use quantile bins (zero-inflated counts keep their own bin); categorical features use category shares.
  - PSI below 0.10 is stable, 0.10–0.25 is moderate, and 0.25 or higher is significant.
  - A feature counts as drifted at PSI ≥ 0.10.
- **Prediction drift**: PSI of the fraud-score distribution.
- **Dataset drift** is declared when at least 30% of features have drifted, or when score PSI is 0.25 or higher.
- **Online**: each API pod keeps a rolling window of the last 2,000 transactions and re-checks every 30 seconds. Results go to Prometheus gauges and `GET /drift`.
- **Offline** (`make drift-report`): PSI, plus a **Kolmogorov–Smirnov** test on numeric features, plus **performance decay** when labels exist (chargebacks arrive later in real life). The output is printed and logged to the MLflow `finguard-drift` experiment.

```
feature                      PSI  status        detail
amount                     0.336  significant   mean 80 -> 143
hour                       0.644  significant   mean 13.5 -> 15.3
channel                    0.151  moderate      online: 33.2% -> 52.0%
new_device                 0.151  moderate      False: 97.8% -> 89.1%
amount_to_avg_ratio        0.002  stable        mean 1.06 -> 1.09     ← relative features are robust to inflation
fraud_score                0.184  moderate      mean 0.00679 -> 0.02
DRIFT DETECTED   : True
performance      : PR-AUC 0.708 (reference 0.846), precision 0.552, recall 0.719
```

## Project layout

```
finguard/
  config.py            paths, MLflow settings, fixed category vocabularies
  data/generate.py     synthetic transactions + fraud typologies + drift scenario
  data/process.py      validation, cleaning, point-in-time aggregates (pandas)
  features.py          shared feature engineering (train == serve)
  train.py             LR vs XGBoost, MLflow tracking, registry, bundle export
  evaluate.py          PR-AUC, thresholds, plots
  explain.py           SHAP explainers + plots
  drift.py             PSI / KS drift detection + offline report CLI
  model.py             serving bundle (model + thresholds + drift profile)
  simulate.py          traffic generator with live precision/recall
  api/                 FastAPI app, schemas, Prometheus metrics, drift monitor
k8s/                   Helm charts (finguard, public-gateway, tempo, loki, hubble-auth), values per environment, Argo CD apps, sealed secrets, chaos/ failure scenarios
compose/               docker-compose Prometheus config (alerts + Grafana files come from the chart)
tests/                 processing leakage, features, drift, evaluation, API, SHAP additivity
scripts/               CI model quality gate, DOKS key/certificate backup and restore
.github/               CI (test → train → gate → build → scan → push to DOCR), Helm lint, Dependabot
terraform/             DOKS cluster + Prometheus block storage volume via HCP Terraform
```

## Production gaps (deliberately out of scope)

- **Feature store** for the behavioural aggregates (Feast + Redis), shared by training and serving.
- **Automated retraining**: drift alert → retrain job → challenger evaluated against the champion → canary rollout (Argo Rollouts), with promotion via the MLflow alias.
- **AuthN/Z and mTLS** on the API, rate limiting, PII handling and audit log retention.
- **Delayed labels**: join chargebacks back to scored transactions for live precision and recall.
- **Model risk management** documentation (SR 11-7 style) and fairness checks.
