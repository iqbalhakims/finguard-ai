# Project Memory
Last updated: 2026-11-14T23:59:40.109Z
Mode: local (no API key — paste this at the start of a new session)

## Files Touched This Session
- /Users/iqbalfauzi/work/finai/finguard/__init__.py
- /Users/iqbalfauzi/work/finai/finguard/config.py
- /Users/iqbalfauzi/work/finai/finguard/data/generate.py
- /Users/iqbalfauzi/work/finai/finguard/data/process.py
- /Users/iqbalfauzi/work/finai/finguard/features.py
- /Users/iqbalfauzi/work/finai/finguard/evaluate.py
- /Users/iqbalfauzi/work/finai/finguard/explain.py
- /Users/iqbalfauzi/work/finai/finguard/drift.py
- /Users/iqbalfauzi/work/finai/finguard/model.py
- /Users/iqbalfauzi/work/finai/finguard/train.py
- /Users/iqbalfauzi/work/finai/finguard/api/schemas.py
- /Users/iqbalfauzi/work/finai/finguard/api/metrics.py
- /Users/iqbalfauzi/work/finai/finguard/api/monitor.py
- /Users/iqbalfauzi/work/finai/finguard/api/app.py
- /Users/iqbalfauzi/work/finai/finguard/simulate.py
- /Users/iqbalfauzi/work/finai/deploy/k8s/extras/servicemonitor.yaml
- /Users/iqbalfauzi/work/finai/deploy/k8s/extras/ingress.yaml
- /Users/iqbalfauzi/work/finai/Makefile
- /Users/iqbalfauzi/work/finai/scripts/gen_prometheusrule.py
- /Users/iqbalfauzi/work/finai/README.md
- /private/tmp/claude-501/-Users-iqbalfauzi-work-finai/1334826f-2260-4a49-bfeb-0b3226a0f34d/scratchpad/grafana.png
- /Users/iqbalfauzi/.claude/projects/-Users-iqbalfauzi-work/memory/project_kube_context_prod.md
- /Users/iqbalfauzi/.claude/projects/-Users-iqbalfauzi-work/memory/project_finguard_ai.md
- /Users/iqbalfauzi/work/finai/finguard/tracking.py

## Commands Run
- `make help && make test 2>&1 | tail -2 && kubectl config current-context`
- `make help | grep -c k8s`
- `cd /Users/iqbalfauzi/.claude/projects/-Users-iqbalfauzi-work/memory && printf '%s\n' '- [Kube context is PROD](project_k`
- `until grep -q "firing" /private/tmp/claude-501/-Users-iqbalfauzi-work-finai/1334826f-2260-4a49-bfeb-0b3226a0f34d/tasks/b`
- `cat /private/tmp/claude-501/-Users-iqbalfauzi-work-finai/1334826f-2260-4a49-bfeb-0b3226a0f34d/tasks/bkx9lmpoz.output; da`
- `until grep -q "drift detected" /private/tmp/claude-501/-Users-iqbalfauzi-work-finai/1334826f-2260-4a49-bfeb-0b3226a0f34d`
- `make k8s-baseline 2>&1 | tail -1; kubectl --context finguard -n finguard get pods --no-headers | awk '{print $1, $3}'; e`
- `kubectl --context finguard -n finguard rollout status deployment/traffic-simulator --timeout=90s; kubectl --context fing`
- `.venv/bin/python -c "`
- `C="--context=do-sgp1-prod"; kubectl $C get nodes -o custom-columns=NAME:.metadata.name,ARCH:.status.nodeInfo.architectur`

## Recent Conversation
…(earlier turns omitted)

 can't see your local images. DigitalOcean Container Registry (DOCR) is the easiest: `doctl kubernetes cluster registry add <cluster>` sets up the pull secrets for you. GHCR works too, with a pull secret. Then point the `images:` entry in [deploy/kustomization.yaml](deploy/kustomization.yaml) at that image.
3. **Don't apply `deploy/` as-is to a production cluster.** Use a separate overlay instead:
   - Drop the `traffic-simulator`. It sends synthetic traffic and would pollute the metrics.
   - Replace the Grafana password, which is `admin` in the secret generator.
   - If the cluster already runs kube-prometheus-stack (you have a folder for it in `work`), skip the bundled Prometheus and Grafana. Use the `ServiceMonitor` and `PrometheusRule` in [deploy/k8s/extras/](deploy/k8s/extras/) and import the dashboard JSON into your existing Grafana.
   - If you keep the bundled Prometheus, give it a disk volume (`do-block-storage`). Right now its data is lost on every restart.
4. **Be careful how you expose it.** The Services are internal-only (ClusterIP), and the API has no authentication. Put it behind an Ingress with auth/TLS, or keep it internal. A DO LoadBalancer also costs about $12/month.
5. **Autoscaling needs metrics-server.** Check whether your cluster has it installed.
6. **Fix `doctl` auth first** (`doctl auth init`), since kubectl can't currently get credentials for that cluster.

Since this runs on synthetic data, I'd put it in its own namespace or on a small separate cluster rather than next to your production workloads. If you want to go ahead later, I can write a `deploy/overlays/doks/` overlay with the changes above, without deploying anything.

USER: why dont we have pipeline to the do registry

ASSISTANT: We don't have one because it wasn't in the 10 features you listed, so I stopped at building the image locally. There was also nothing to attach it to:

- **No repo.** `finai` isn't its own git repository yet. It's an untracked folder inside `work`, with no GitHub remote for a workflow to run from.
- **No registry details.** I don't know your DOCR registry name, and CI needs a DigitalOcean access token stored as a secret.
- **The model isn't in git.** The image bakes in `artifacts/model/`, which is gitignored, so the pipeline has to produce the model itself.

A pipeline would follow the same pattern as your car-sales-crm `deploy.yml`:

1. **Test:** run `pytest`.
2. **Train:** generate the data and train inside CI, which takes about 20 seconds and gives the same model every time. With real data you'd pull `finguard-fraud-detector@champion` from a shared MLflow server instead.
3. **Build:** build a `linux/amd64` image, which also fixes the Mac arm64 mismatch.
4. **Scan:** run Trivy on the image.
5. **Push:** push to `registry.digitalocean.com/<registry>/finguard-api:<git-sha>`.
6. **Deploy (optional):** update the image tag in a DOKS overlay and apply it, or leave deploying as a manual step.

If you want it, I need three things from you:
- your DOCR registry name;
- whether `finai` should get its own GitHub repo;
- whether the pipeline should stop at pushing the image or also deploy to DOKS.

I won't create anything until you say so.