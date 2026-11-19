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
        minikube-start k8s-load k8s-deploy k8s-status k8s-forward k8s-drift k8s-baseline k8s-delete helm-lint \
        argocd-bootstrap argocd-status argocd-ui doks-seal-grafana doks-volume doks-set-host doks-public-ip \
        doks-cert-status doks-status doks-forward doks-seal-cloudflare doks-seal-otel trace-demo doks-backup doks-restore doks-up doks-down

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

CHART := k8s/charts/finguard
HELM  := helm --kube-context $(KUBE_CONTEXT)

k8s-deploy: ## Install/upgrade the Helm chart on minikube (k8s/values/minikube.yaml)
	$(KUBECTL) create namespace finguard --dry-run=client -o yaml | $(KUBECTL) apply -f -
	$(KUBECTL) label namespace finguard pod-security.kubernetes.io/enforce=restricted --overwrite
	$(HELM) upgrade --install finguard $(CHART) -n finguard -f k8s/values/minikube.yaml --wait --timeout 5m

k8s-status: ## Show pods, HPA and services
	$(KUBECTL) -n finguard get pods,svc,hpa,pdb

k8s-forward: ## Port-forward API :8080, Grafana :3300, Prometheus :9095
	@echo "API http://localhost:8080/docs | Grafana http://localhost:3300 | Prometheus http://localhost:9095"
	$(KUBECTL) -n finguard port-forward svc/finguard-api 8080:80 & \
	$(KUBECTL) -n finguard port-forward svc/grafana 3300:3000 & \
	$(KUBECTL) -n finguard port-forward svc/prometheus 9095:9090 & \
	wait

k8s-drift: ## Switch the minikube simulator to drifted traffic
	$(HELM) upgrade finguard $(CHART) -n finguard -f k8s/values/minikube.yaml --set simulator.drift=true

k8s-baseline: ## Switch the minikube simulator back to baseline traffic
	$(HELM) upgrade finguard $(CHART) -n finguard -f k8s/values/minikube.yaml --set simulator.drift=false

k8s-delete: ## Uninstall the FinGuard release from minikube
	$(HELM) uninstall finguard -n finguard --ignore-not-found

helm-lint: ## Lint and render the charts with every values file
	helm lint $(CHART) -f k8s/values/minikube.yaml
	helm lint $(CHART) -f k8s/values/doks.yaml
	helm lint k8s/charts/public-gateway -f k8s/values/public-gateway.yaml
	helm template finguard $(CHART) -n finguard -f k8s/values/doks.yaml > /dev/null
	helm template finguard $(CHART) -n finguard -f k8s/values/minikube.yaml > /dev/null
	helm template public-gateway k8s/charts/public-gateway -n monitoring -f k8s/values/public-gateway.yaml --set host=ci.example.com > /dev/null
	helm lint k8s/charts/tempo
	helm template tempo k8s/charts/tempo -n observability > /dev/null

# ----------------------------------------------------------------------------- DOKS via GitOps (Argo CD + Helm)
# After bootstrap, deploying = edit k8s/ (chart or values/doks.yaml), commit, push.
DOKS_CONTEXT    ?= do-sgp1-finguard
DOKS_KUBECTL    := kubectl --context $(DOKS_CONTEXT)
DOKS_CLUSTER    ?= finguard
KPS_VALUES      := k8s/values/kube-prometheus-stack.yaml
GATEWAY_VALUES  := k8s/values/public-gateway.yaml
SECRETS_DIR     := k8s/secrets
BACKUP_DIR      := k8s/.secrets
ARGOCD_CHART_VERSION := 10.11.6

argocd-bootstrap: ## Install Argo CD once with Helm, then apply the root app (everything else is GitOps)
	@# Fixed admin password from k8s/.secrets/ (gitignored, created on first run), bcrypt-hashed
	@# here, so the login stays the same across cluster rebuilds. Read via stdin, never argv.
	@mkdir -p $(BACKUP_DIR); test -f $(BACKUP_DIR)/argocd-admin-password || { \
	  umask 077; openssl rand -base64 24 | tr -d '/+=\n' > $(BACKUP_DIR)/argocd-admin-password; }
	@hash=$$(htpasswd -niBC 10 '' < $(BACKUP_DIR)/argocd-admin-password | tr -d ':\n' | sed 's/^\$$2y/$$2a/'); \
	test -n "$$hash" || { echo "could not hash the Argo CD password (htpasswd missing?)"; exit 1; }; \
	echo "helm upgrade --install argocd (admin password: $(BACKUP_DIR)/argocd-admin-password)"; \
	helm --kube-context $(DOKS_CONTEXT) upgrade --install argocd argo-cd \
	  --repo https://argoproj.github.io/argo-helm --version $(ARGOCD_CHART_VERSION) \
	  -n argocd --create-namespace -f k8s/bootstrap/argocd-values.yaml \
	  --set-string configs.secret.argocdServerAdminPassword="$$hash" --wait --timeout 10m
	@# Only valid until the first fixed password is set; remove it so nobody uses a stale one.
	$(DOKS_KUBECTL) -n argocd delete secret argocd-initial-admin-secret --ignore-not-found
	$(DOKS_KUBECTL) apply -f k8s/argocd/root.yaml

argocd-status: ## Sync and health status of all Argo CD applications
	$(DOKS_KUBECTL) -n argocd get applications

argocd-ui: ## Argo CD UI via port-forward at http://localhost:8443 (public: https://argo.iqbalhakim.site)
	@echo "user: admin   password: in $(BACKUP_DIR)/argocd-admin-password (pbcopy < $(BACKUP_DIR)/argocd-admin-password)"
	$(DOKS_KUBECTL) -n argocd port-forward svc/argocd-server 8443:80

doks-seal-grafana: ## Seal a random Grafana admin login into k8s/secrets/grafana-admin.yaml (needs kubeseal)
	@command -v kubeseal >/dev/null || { echo "kubeseal not found: brew install kubeseal"; exit 1; }
	@mkdir -p $(BACKUP_DIR); test -f $(BACKUP_DIR)/grafana-admin-password || { \
	  umask 077; openssl rand -base64 24 | tr -d '/+=\n' > $(BACKUP_DIR)/grafana-admin-password; }
	@$(DOKS_KUBECTL) create secret generic grafana-admin -n monitoring --dry-run=client -o yaml \
	  --from-literal=admin-user=admin --from-file=admin-password=$(BACKUP_DIR)/grafana-admin-password \
	  | kubeseal --context $(DOKS_CONTEXT) --format yaml > $(SECRETS_DIR)/grafana-admin.yaml.tmp; \
	test -s $(SECRETS_DIR)/grafana-admin.yaml.tmp && mv $(SECRETS_DIR)/grafana-admin.yaml.tmp $(SECRETS_DIR)/grafana-admin.yaml \
	  && echo "wrote $(SECRETS_DIR)/grafana-admin.yaml (commit it). Login: admin / $(BACKUP_DIR)/grafana-admin-password"

doks-seal-cloudflare: ## Seal your Cloudflare API token for external-dns (prompts; input is hidden)
	@command -v kubeseal >/dev/null || { echo "kubeseal not found: brew install kubeseal"; exit 1; }
	@printf 'Cloudflare API token (Zone > DNS > Edit, iqbalhakim.site only; hidden): '; \
	stty -echo; read -r token; stty echo; echo; test -n "$$token" || { echo "no token entered"; exit 1; }; \
	printf '%s' "$$token" | $(DOKS_KUBECTL) create secret generic cloudflare-api-token -n external-dns \
	  --dry-run=client -o yaml --from-file=api-token=/dev/stdin \
	  | kubeseal --context $(DOKS_CONTEXT) --format yaml > $(SECRETS_DIR)/cloudflare-api-token.yaml.tmp; \
	test -s $(SECRETS_DIR)/cloudflare-api-token.yaml.tmp && mv $(SECRETS_DIR)/cloudflare-api-token.yaml.tmp $(SECRETS_DIR)/cloudflare-api-token.yaml \
	  && echo "wrote $(SECRETS_DIR)/cloudflare-api-token.yaml (commit it)"

doks-seal-otel: ## Seal a random OTLP ingest token for https://otel.iqbalhakim.site (plain copy: k8s/.secrets/otel-ingest-token)
	@command -v kubeseal >/dev/null || { echo "kubeseal not found: brew install kubeseal"; exit 1; }
	@mkdir -p $(BACKUP_DIR); test -f $(BACKUP_DIR)/otel-ingest-token || { \
	  umask 077; openssl rand -hex 32 | tr -d '\n' > $(BACKUP_DIR)/otel-ingest-token; }
	@$(DOKS_KUBECTL) create secret generic otel-ingest-token -n observability --dry-run=client -o yaml \
	  --from-file=token=$(BACKUP_DIR)/otel-ingest-token \
	  | kubeseal --context $(DOKS_CONTEXT) --format yaml > $(SECRETS_DIR)/otel-ingest-token.yaml.tmp; \
	test -s $(SECRETS_DIR)/otel-ingest-token.yaml.tmp && mv $(SECRETS_DIR)/otel-ingest-token.yaml.tmp $(SECRETS_DIR)/otel-ingest-token.yaml \
	  && echo "wrote $(SECRETS_DIR)/otel-ingest-token.yaml (commit it). Clients send: Authorization: Bearer <$(BACKUP_DIR)/otel-ingest-token>"

trace-demo: ## Send traced demo checkouts laptop -> API (needs `make doks-forward` running); prints trace IDs for Tempo
	$(PY) scripts/trace_demo.py --count 6

doks-backup: ## Back up the Sealed Secrets keys + TLS certificate to k8s/.secrets/ (gitignored)
	$(PY) scripts/doks_secrets.py backup --context $(DOKS_CONTEXT) --dir $(BACKUP_DIR)

doks-restore: ## Restore the Sealed Secrets keys + TLS certificate into a fresh cluster
	$(PY) scripts/doks_secrets.py restore --context $(DOKS_CONTEXT) --dir $(BACKUP_DIR)

doks-down: doks-backup ## Before switching the cluster off: back up keys/cert and remove the load balancer
	@# Removing the cluster would orphan the Gateway's load balancer (still billed), so delete
	@# the Gateway while the cluster runs. Deleting the Argo CD Applications first stops
	@# self-heal from recreating it (no finalizers: the workloads themselves are left alone).
	-$(DOKS_KUBECTL) -n argocd delete applications --all --wait=true
	$(DOKS_KUBECTL) -n monitoring delete gateway public-gateway --ignore-not-found --wait=true
	@echo "waiting for DigitalOcean to remove load balancer finguard-public..."; \
	for i in $$(seq 1 30); do doctl compute load-balancer list --format Name --no-header | grep -qx finguard-public || break; sleep 10; done; \
	if doctl compute load-balancer list --format Name --no-header | grep -qx finguard-public; then \
	  echo "WARNING: load balancer finguard-public still exists - check before switching off"; exit 1; fi
	@echo "Load balancer removed. Now in HCP Terraform (workspace finguard-ai): set cluster_enabled = false and start a run."
	@echo "The Prometheus volume (metrics history) is kept."

doks-up: ## After HCP created the cluster (cluster_enabled=true): kubeconfig, restore keys/cert, Argo CD
	doctl kubernetes cluster kubeconfig save $(DOKS_CLUSTER) --set-current-context=false
	$(MAKE) doks-restore
	$(MAKE) argocd-bootstrap
	@echo "Argo CD is syncing (make argocd-status). external-dns points grafana./argo.iqbalhakim.site at the new IP in ~2 min."

doks-volume: ## Put the Terraform-created Prometheus volume ID into k8s/values/kube-prometheus-stack.yaml
	@id=$$(doctl compute volume list --format ID,Name --no-header | awk '$$2=="finguard-prometheus"{print $$1}'); \
	test -n "$$id" || { echo "volume finguard-prometheus not found: push terraform/ to main (HCP Terraform auto-applies) first"; exit 1; }; \
	sed -i.orig "s|^        volumeHandle: .*|        volumeHandle: $$id   # terraform output prometheus_volume_id|" $(KPS_VALUES) && \
	mv $(KPS_VALUES).orig /tmp/; echo "finguard-prometheus -> $$id"

doks-set-host: ## Set the public Grafana hostname: make doks-set-host HOST=grafana.example.com
	@test -n "$(HOST)" || { echo "usage: make doks-set-host HOST=grafana.yourdomain.com"; exit 1; }
	sed -i.orig "s|^host: .*|host: $(HOST)|" $(GATEWAY_VALUES) && mv $(GATEWAY_VALUES).orig /tmp/
	sed -i.orig -e "s|^      domain: .*|      domain: $(HOST)|" \
	  -e 's|^      root_url: .*|      root_url: "https://%(domain)s/"|' $(KPS_VALUES) && mv $(KPS_VALUES).orig /tmp/
	@echo "host set in $(GATEWAY_VALUES) and $(KPS_VALUES): commit and push"

doks-public-ip: ## Public IP of the Cilium Gateway (DNS A record for the public host)
	@$(DOKS_KUBECTL) -n monitoring get gateway public-gateway -o jsonpath='{.status.addresses[0].value}{"\n"}'

doks-cert-status: ## Gateway, routes and Let's Encrypt certificate status
	$(DOKS_KUBECTL) -n monitoring get gateway,httproute,certificate

doks-status: ## FinGuard and monitoring pods, storage, gateway
	$(DOKS_KUBECTL) -n finguard get pods,svc
	$(DOKS_KUBECTL) -n monitoring get pods,pvc,gateway

doks-forward: ## Port-forward on DOKS: API :8080, Grafana :3300, Prometheus :9095
	@echo "API http://localhost:8080/docs | Grafana http://localhost:3300 | Prometheus http://localhost:9095"
	$(DOKS_KUBECTL) -n finguard port-forward svc/finguard-api 8080:80 & \
	$(DOKS_KUBECTL) -n monitoring port-forward svc/grafana 3300:80 & \
	$(DOKS_KUBECTL) -n monitoring port-forward svc/kps-prometheus 9095:9090 & \
	wait
