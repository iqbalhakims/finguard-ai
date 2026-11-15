# FinGuard AI — common workflows.  `make help` lists targets.

PYTHON      ?= python3.12
VENV        ?= .venv
PY          := $(VENV)/bin/python
IMAGE       ?= finguard-api
VERSION     ?= 0.1.0
# Kubernetes targets always pass an explicit --context so they can never hit
# whatever cluster your kubeconfig currently points at.
MINIKUBE_PROFILE ?= finguard
KUBE_CONTEXT     ?= $(MINIKUBE_PROFILE)
KUBECTL          := kubectl --context $(KUBE_CONTEXT)
API_PORT         ?= 8000

.DEFAULT_GOAL := help
.PHONY: help setup data train drift-data drift-report test serve simulate simulate-drift mlflow-ui \
        pipeline docker-build up down compose-drift compose-baseline \
        minikube-start k8s-load k8s-deploy k8s-status k8s-forward k8s-drift k8s-baseline k8s-delete prometheusrule \
        doks-secrets doks-volume doks-deploy doks-public-ip doks-status doks-forward

help: ## Show this help
	@grep -E '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# ----------------------------------------------------------------------------- ML
setup: ## Create the virtualenv and install dependencies
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/pip install -q --upgrade pip
	$(VENV)/bin/pip install -q -r requirements.txt

data: ## Generate synthetic transactions and process them with pandas
	$(PY) -m finguard.data.generate
	$(PY) -m finguard.data.process

train: ## Train LR + XGBoost, log to MLflow, register champion, export serving bundle
	$(PY) -m finguard.train

drift-data: ## Generate a drifted "production" dataset
	$(PY) -m finguard.data.generate --drift

drift-report: ## Offline drift + performance report for the drifted dataset
	$(PY) -m finguard.drift

test: ## Run the test suite
	$(PY) -m pytest -q

pipeline: data train drift-data drift-report test ## Run the full ML pipeline end to end

serve: ## Run the API locally with auto-reload
	$(VENV)/bin/uvicorn finguard.api.app:app --reload --port $(API_PORT)

simulate: ## Stream baseline traffic to the local API
	$(PY) -m finguard.simulate --url http://localhost:$(API_PORT) --rps 20

simulate-drift: ## Stream drifted traffic to the local API
	$(PY) -m finguard.simulate --url http://localhost:$(API_PORT) --rps 20 --drift

mlflow-ui: ## Browse local MLflow runs and the model registry (http://localhost:5002)
	MLFLOW_SERVER_ENABLE_JOB_EXECUTION=false $(VENV)/bin/mlflow ui --backend-store-uri sqlite:///mlflow.db --port 5002

# ----------------------------------------------------------------------------- Docker
docker-build: ## Build the API image (requires artifacts/model from `make train`)
	@test -f artifacts/model/model.joblib || (echo "artifacts/model missing: run 'make data train' first" && exit 1)
	docker build -t $(IMAGE):$(VERSION) -t $(IMAGE):latest .

up: docker-build ## Start API, simulator, Prometheus, Grafana, MLflow with docker compose
	FINGUARD_VERSION=$(VERSION) API_PORT=$(API_PORT) docker compose up -d
	@echo "API http://localhost:$(API_PORT)/docs | Grafana http://localhost:3300 | Prometheus http://localhost:9095 | MLflow http://localhost:5001"

down: ## Stop the compose stack (keeps volumes)
	docker compose down

compose-drift: ## Switch the compose simulator to drifted traffic
	-curl -s -X POST http://localhost:$(API_PORT)/drift/reset
	SIMULATOR_DRIFT=true docker compose up -d simulator

compose-baseline: ## Switch the compose simulator back to baseline traffic
	-curl -s -X POST http://localhost:$(API_PORT)/drift/reset
	SIMULATOR_DRIFT=false docker compose up -d simulator

# ----------------------------------------------------------------------------- Kubernetes
minikube-start: ## Start a dedicated minikube profile without switching your kube context
	minikube start -p $(MINIKUBE_PROFILE) --keep-context --cpus 4 --memory 4096
	minikube -p $(MINIKUBE_PROFILE) addons enable metrics-server

k8s-load: docker-build ## Load the API image into minikube
	minikube -p $(MINIKUBE_PROFILE) image load $(IMAGE):$(VERSION)

k8s-deploy: ## Deploy API + simulator + Prometheus + Grafana (kustomize)
	$(KUBECTL) apply -k deploy/
	$(KUBECTL) -n finguard rollout status deployment/finguard-api --timeout=180s

k8s-status: ## Show pods, HPA and services
	$(KUBECTL) -n finguard get pods,svc,hpa,pdb

k8s-forward: ## Port-forward API :8080, Grafana :3300, Prometheus :9095
	@echo "API http://localhost:8080/docs | Grafana http://localhost:3300 | Prometheus http://localhost:9095"
	$(KUBECTL) -n finguard port-forward svc/finguard-api 8080:80 & \
	$(KUBECTL) -n finguard port-forward svc/grafana 3300:3000 & \
	$(KUBECTL) -n finguard port-forward svc/prometheus 9095:9090 & \
	wait

k8s-drift: ## Switch the in-cluster simulator to drifted traffic
	$(KUBECTL) -n finguard set env deployment/traffic-simulator SIMULATOR_DRIFT=true

k8s-baseline: ## Switch the in-cluster simulator back to baseline traffic
	$(KUBECTL) -n finguard set env deployment/traffic-simulator SIMULATOR_DRIFT=false

k8s-delete: ## Remove the FinGuard stack from the cluster
	$(KUBECTL) delete -k deploy/ --ignore-not-found

# ----------------------------------------------------------------------------- DOKS (terraform/ + overlays/doks)
DOKS_CONTEXT ?= do-sgp1-finguard
DOKS_OVERLAY := overlays/doks

doks-secrets: ## Create a random Grafana admin password for the DOKS overlay (once)
	@test -f $(DOKS_OVERLAY)/grafana.env || { \
	  umask 077; printf 'password=%s\n' "$$(openssl rand -base64 24 | tr -d '/+=')" > $(DOKS_OVERLAY)/grafana.env; \
	  echo "created $(DOKS_OVERLAY)/grafana.env (gitignored)"; }

doks-volume: ## Put the Terraform-created volume IDs into the overlay PersistentVolumes (after terraform apply)
	@for pair in finguard-prometheus:prometheus-storage.yaml finguard-caddy:public-grafana.yaml; do \
	  name=$${pair%%:*}; file=$(DOKS_OVERLAY)/$${pair#*:}; \
	  id=$$(doctl compute volume list --format ID,Name --no-header | awk -v n=$$name '$$2==n{print $$1}'); \
	  test -n "$$id" || { echo "volume $$name not found: run terraform apply first"; exit 1; }; \
	  sed -i.orig "s|volumeHandle: [^ ]*|volumeHandle: $$id|" $$file && mv $$file.orig /tmp/; \
	  echo "$$name -> $$id"; \
	done

doks-deploy: ## Deploy FinGuard to the DOKS cluster (context $(DOKS_CONTEXT))
	@test -f $(DOKS_OVERLAY)/grafana.env || { echo "run 'make doks-secrets' first"; exit 1; }
	@! grep -q "REPLACE_WITH_" $(DOKS_OVERLAY)/prometheus-storage.yaml $(DOKS_OVERLAY)/public-grafana.yaml || { echo "run 'make doks-volume' first"; exit 1; }
	@! grep -q "PUBLIC_HOST=grafana.example.com" $(DOKS_OVERLAY)/public.env || { echo "set PUBLIC_HOST in $(DOKS_OVERLAY)/public.env first"; exit 1; }
	kubectl --context $(DOKS_CONTEXT) apply -k $(DOKS_OVERLAY)
	kubectl --context $(DOKS_CONTEXT) -n finguard rollout status deployment/finguard-api --timeout=300s

doks-public-ip: ## Load balancer IP to use in the DNS A record for PUBLIC_HOST
	@kubectl --context $(DOKS_CONTEXT) -n finguard get svc grafana-public -o jsonpath='{.status.loadBalancer.ingress[0].ip}{"\n"}'

doks-status: ## Pods, storage and services on DOKS
	kubectl --context $(DOKS_CONTEXT) -n finguard get pods,pvc,svc
	kubectl --context $(DOKS_CONTEXT) get pv finguard-prometheus finguard-caddy

doks-forward: ## Port-forward the DOKS deployment (API :8080, Grafana :3300, Prometheus :9095)
	$(MAKE) k8s-forward KUBE_CONTEXT=$(DOKS_CONTEXT)

prometheusrule: ## Regenerate deploy/k8s/extras/prometheusrule.yaml from alerts.yml
	$(PY) scripts/gen_prometheusrule.py
