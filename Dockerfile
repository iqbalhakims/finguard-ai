# syntax=docker/dockerfile:1.7
# FinGuard inference service. The model bundle (artifacts/model) is baked into
# the image, so every image tag is one immutable, auditable model version.
# Build after training:  make train && make docker-build

ARG PYTHON_VERSION=3.12

FROM python:${PYTHON_VERSION}-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
COPY requirements-serve.txt .
RUN pip install -r requirements-serve.txt

FROM python:${PYTHON_VERSION}-slim AS runtime
LABEL org.opencontainers.image.title="finguard-api" \
      org.opencontainers.image.description="FinGuard AI fraud scoring API"
# Apply pending Debian security fixes (the CI Trivy gate fails on fixable HIGH/CRITICAL CVEs);
# libgomp: OpenMP runtime used by XGBoost
RUN apt-get update \
 && apt-get upgrade -y --no-install-recommends \
 && apt-get install -y --no-install-recommends libgomp1 \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin finguard
COPY --from=builder /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    FINGUARD_HOME=/app \
    FINGUARD_MODEL_DIR=/app/artifacts/model \
    MPLCONFIGDIR=/tmp/matplotlib
WORKDIR /app
COPY finguard ./finguard
COPY artifacts/model ./artifacts/model
USER 10001
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=2)"]
# One worker per container; scale out with replicas (keeps Prometheus metrics per-pod).
CMD ["uvicorn", "finguard.api.app:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--timeout-graceful-shutdown", "20"]
