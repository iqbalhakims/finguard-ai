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
| 8 | Kubernetes | [deploy/kustomization.yaml](deploy/kustomization.yaml), [deploy/k8s/](deploy/k8s/) |
| 9 | Prometheus & Grafana | [finguard/api/metrics.py](finguard/api/metrics.py), [deploy/monitoring/](deploy/monitoring/) |
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

## 8 · Kubernetes

```bash
make minikube-start   # dedicated profile "finguard", started with --keep-context
make k8s-load         # build the image and load it into minikube
make k8s-deploy       # kubectl --context finguard apply -k deploy/
make k8s-forward      # API :8080 · Grafana :3300 · Prometheus :9095
make k8s-drift        # switch the in-cluster simulator to drifted traffic
```

> All `k8s-*` targets pass an explicit `--context` (default `finguard`), so they
> never deploy to whatever cluster your kubeconfig currently points at. To deploy
> elsewhere, run `make k8s-deploy KUBE_CONTEXT=<ctx>` after pushing the image to a
> registry and updating `images:` in [deploy/kustomization.yaml](deploy/kustomization.yaml).

What gets deployed (namespace `finguard`):

- The namespace enforces the **restricted** Pod Security Standard. Every pod runs as non-root with a read-only root filesystem (Grafana aside), all capabilities dropped, and a seccomp profile.
- The API runs **2 replicas** with rolling updates (`maxUnavailable: 0`), startup, readiness and liveness probes, a `preStop` drain, topology spread, an **HPA** scaling 2→6 at 70% CPU (needs metrics-server), and a **PDB** with `minAvailable: 1`.
- A traffic simulator Deployment keeps the dashboards alive.
- An in-namespace **Prometheus** discovers pods through annotations. It scrapes each pod rather than the Service, so per-replica counters and drift windows aren't mixed, and it uses a namespaced Role only.
- **Grafana** is provisioned from ConfigMaps.
- Monitoring configs are generated by kustomize from the **same files docker-compose uses**, with a hashed name so editing them triggers a rollout.
- [deploy/k8s/extras/](deploy/k8s/extras/) holds a `ServiceMonitor` and a `PrometheusRule` (generated from `alerts.yml` by `make prometheusrule`) for clusters running kube-prometheus-stack, plus an example Ingress.

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

Alert rules ([alerts.yml](deploy/monitoring/prometheus/alerts.yml)):

| Alert | Fires when |
|---|---|
| `FinGuardApiDown` | a scrape target is down |
| `FinGuardHighErrorRate` | 5xx rate is above 2% |
| `FinGuardHighLatencyP95` | p95 latency is above 100 ms (card-authorisation SLA) |
| `FinGuardFraudFlagRateSpike` | more than 5% of transactions are flagged |
| `FinGuardDataDrift` | the drift verdict is positive |
| `FinGuardPredictionDrift` | score PSI is 0.25 or higher |

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
deploy/
  kustomization.yaml   whole stack for Kubernetes
  k8s/app|monitoring   manifests;  k8s/extras: ServiceMonitor, PrometheusRule, Ingress
  monitoring/          Prometheus config + alerts, Grafana provisioning + dashboard (shared by compose & k8s)
tests/                 processing leakage, features, drift, evaluation, API, SHAP additivity
```

## Production gaps (deliberately out of scope)

- **Feature store** for the behavioural aggregates (Feast + Redis), shared by training and serving.
- **Automated retraining**: drift alert → retrain job → challenger evaluated against the champion → canary rollout (Argo Rollouts), with promotion via the MLflow alias.
- **AuthN/Z and mTLS** on the API, rate limiting, PII handling and audit log retention.
- **Delayed labels**: join chargebacks back to scored transactions for live precision and recall.
- **Model risk management** documentation (SR 11-7 style) and fairness checks.
