"""FinGuard inference service (FastAPI).

Endpoints
---------
POST /predict             score one transaction (``?explain=true`` adds SHAP reasons)
POST /predict/batch       score up to 1,000 transactions
GET  /model               loaded model metadata, thresholds and offline metrics
GET  /drift               latest drift report for the live traffic window
POST /drift/reset         clear the drift window (e.g. after a planned change)
GET  /metrics             Prometheus metrics
GET  /health/live         liveness probe
GET  /health/ready        readiness probe (model loaded)

Run locally::

    uvicorn finguard.api.app:app --reload
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from finguard import __version__
from finguard.api import metrics
from finguard.api.monitor import DriftMonitor
from finguard.api.schemas import BatchRequest, BatchResponse, Prediction, Transaction
from finguard.config import MODEL_DIR
from finguard.drift import monitoring_frame
from finguard.explain import ShapExplainer
from finguard.features import build_features
from finguard.model import ModelBundle

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("finguard.api")

MODEL_PATH = Path(os.getenv("FINGUARD_MODEL_DIR", MODEL_DIR))
DRIFT_WINDOW_SIZE = int(os.getenv("DRIFT_WINDOW_SIZE", "2000"))
DRIFT_MIN_SAMPLES = int(os.getenv("DRIFT_MIN_SAMPLES", "200"))
DRIFT_CHECK_INTERVAL = float(os.getenv("DRIFT_CHECK_INTERVAL_SECONDS", "30"))
DRIFT_SHARE = float(os.getenv("DRIFT_SHARE_THRESHOLD", "0.3"))
EXPLAIN_TOP_K = int(os.getenv("EXPLAIN_TOP_K", "5"))


class FraudService:
    def __init__(self, bundle: ModelBundle):
        self.bundle = bundle
        self.explainer = ShapExplainer(bundle.estimator, bundle.background)
        self.monitor = DriftMonitor(
            bundle.drift_profile,
            window_size=DRIFT_WINDOW_SIZE,
            min_samples=DRIFT_MIN_SAMPLES,
            drift_share=DRIFT_SHARE,
        )

    @staticmethod
    def to_frame(transactions: list[Transaction]) -> pd.DataFrame:
        rows = []
        for t in transactions:
            row = t.model_dump()
            # Keep the cardholder's local wall-clock time (see features.wall_clock).
            row["timestamp"] = t.timestamp.replace(tzinfo=None)
            rows.append(row)
        return pd.DataFrame(rows)

    def score(self, transactions: list[Transaction], explain: bool = False) -> list[Prediction]:
        b = self.bundle
        df = self.to_frame(transactions)
        with metrics.INFERENCE_LATENCY.labels(stage="predict").time():
            features = build_features(df)
            scores = b.predict_proba(features)

        explanations = None
        if explain:
            with metrics.INFERENCE_LATENCY.labels(stage="explain").time():
                explanations = self.explainer.top_contributions(features, EXPLAIN_TOP_K)

        self.monitor.observe(monitoring_frame(df), scores)

        predictions = []
        for i, (txn, score) in enumerate(zip(transactions, scores)):
            score = float(score)
            decision = b.decide(score)
            metrics.PREDICTIONS.labels(decision=decision, model_version=b.version).inc()
            metrics.FRAUD_SCORE.observe(score)
            metrics.SCORED_AMOUNT.labels(decision=decision).inc(txn.amount)
            if decision == "block":
                logger.info("blocked transaction_id=%s score=%.4f", txn.transaction_id, score)
            predictions.append(
                Prediction(
                    transaction_id=txn.transaction_id,
                    fraud_probability=round(score, 6),
                    decision=decision,
                    is_fraud=decision != "approve",
                    thresholds={"review": b.threshold, "block": b.block_threshold},
                    model_name=b.name,
                    model_version=b.version,
                    explanation=explanations[i] if explanations else None,
                )
            )
        return predictions


async def _drift_loop(service: FraudService) -> None:
    while True:
        await asyncio.sleep(DRIFT_CHECK_INTERVAL)
        try:
            report = await run_in_threadpool(service.monitor.evaluate)
            if report.get("drift_detected"):
                logger.warning("data drift detected: %s", report["drifted_features"])
        except Exception:  # never let monitoring kill the service
            logger.exception("drift check failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.service = None
    task = None
    try:
        bundle = ModelBundle.load(MODEL_PATH)
        service = FraudService(bundle)
        app.state.service = service
        metrics.MODEL_INFO.labels(bundle.name, bundle.version, bundle.model_type).set(1)
        metrics.MODEL_THRESHOLD.labels("review").set(bundle.threshold)
        metrics.MODEL_THRESHOLD.labels("block").set(bundle.block_threshold)
        task = asyncio.create_task(_drift_loop(service))
        logger.info(
            "loaded model %s v%s (%s) review>=%.4f block>=%.4f",
            bundle.name,
            bundle.version,
            bundle.model_type,
            bundle.threshold,
            bundle.block_threshold,
        )
    except Exception:
        # Stay up so liveness passes and the error is visible; readiness reports 503.
        logger.exception("failed to load model bundle from %s", MODEL_PATH)
    yield
    if task:
        task.cancel()


app = FastAPI(
    title="FinGuard AI — Fraud Detection API",
    version=__version__,
    description="Real-time card-transaction fraud scoring with SHAP explanations and drift monitoring.",
    lifespan=lifespan,
)


@app.middleware("http")
async def prometheus_middleware(request: Request, call_next):
    start = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        route = request.scope.get("route")
        path = getattr(route, "path", "unmatched")
        if path != "/metrics":
            metrics.HTTP_LATENCY.labels(request.method, path).observe(time.perf_counter() - start)
            metrics.HTTP_REQUESTS.labels(request.method, path, str(status)).inc()


def _service(request: Request) -> FraudService:
    service = request.app.state.service
    if service is None:
        raise HTTPException(status_code=503, detail="model not loaded")
    return service


@app.get("/", include_in_schema=False)
def root() -> dict:
    return {"service": "finguard-api", "version": __version__, "docs": "/docs"}


@app.get("/health/live", tags=["ops"])
def live() -> dict:
    return {"status": "alive"}


@app.get("/health/ready", tags=["ops"])
def ready(request: Request) -> dict:
    service = _service(request)
    return {"status": "ready", "model_version": service.bundle.version}


@app.get("/metrics", include_in_schema=False)
def prometheus_metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/model", tags=["model"])
def model_info(request: Request) -> dict:
    meta = _service(request).bundle.metadata
    return {k: v for k, v in meta.items() if k != "features"} | {"n_features": len(meta["features"])}


@app.post("/predict", response_model=Prediction, response_model_exclude_none=True, tags=["scoring"])
def predict(
    transaction: Transaction,
    request: Request,
    explain: bool = Query(False, description="Include SHAP feature contributions"),
) -> Prediction:
    return _service(request).score([transaction], explain=explain)[0]


@app.post("/predict/batch", response_model=BatchResponse, response_model_exclude_none=True, tags=["scoring"])
def predict_batch(
    batch: BatchRequest,
    request: Request,
    explain: bool = Query(False, description="Include SHAP feature contributions"),
) -> BatchResponse:
    predictions = _service(request).score(batch.transactions, explain=explain)
    return BatchResponse(
        count=len(predictions),
        flagged=sum(p.is_fraud for p in predictions),
        predictions=predictions,
    )


@app.get("/drift", tags=["monitoring"])
def drift(request: Request, refresh: bool = Query(False, description="Recompute now instead of returning the last check")) -> dict:
    monitor = _service(request).monitor
    return monitor.evaluate() if refresh else monitor.last_report


@app.post("/drift/reset", tags=["monitoring"])
def drift_reset(request: Request) -> dict:
    _service(request).monitor.reset()
    return {"status": "reset"}
