"""Prometheus metrics exposed on ``/metrics``.

Run one Uvicorn worker per container and scale with replicas: each pod is
scraped individually, so no multiprocess registry is needed.
"""

from prometheus_client import Counter, Gauge, Histogram

HTTP_REQUESTS = Counter(
    "finguard_http_requests",
    "HTTP requests handled",
    ["method", "route", "status"],
)
HTTP_LATENCY = Histogram(
    "finguard_http_request_duration_seconds",
    "End-to-end HTTP request latency",
    ["method", "route"],
    buckets=(0.002, 0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 1.0, 2.5),
)
INFERENCE_LATENCY = Histogram(
    "finguard_inference_duration_seconds",
    "Time spent in feature building + model scoring (stage=predict) or SHAP (stage=explain)",
    ["stage"],
    buckets=(0.0005, 0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25),
)
PREDICTIONS = Counter(
    "finguard_predictions",
    "Scored transactions by decision",
    ["decision", "model_version"],
)
FRAUD_SCORE = Histogram(
    "finguard_fraud_score",
    "Distribution of predicted fraud probabilities",
    buckets=(0.01, 0.02, 0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.99, 1.0),
)
SCORED_AMOUNT = Counter(
    "finguard_scored_amount",
    "Transaction value scored, by decision (currency units)",
    ["decision"],
)
MODEL_INFO = Gauge(
    "finguard_model_info",
    "Loaded model (value is always 1)",
    ["model_name", "model_version", "model_type"],
)
MODEL_THRESHOLD = Gauge("finguard_model_threshold", "Decision thresholds", ["decision"])

FEATURE_PSI = Gauge("finguard_feature_drift_psi", "Population Stability Index vs training reference", ["feature"])
PREDICTION_PSI = Gauge("finguard_prediction_drift_psi", "PSI of the fraud-score distribution vs reference")
DRIFT_DETECTED = Gauge("finguard_drift_detected", "1 when drift is detected in the current window")
DRIFT_SHARE = Gauge("finguard_drift_share", "Share of monitored features with significant drift")
DRIFT_WINDOW = Gauge("finguard_drift_window_samples", "Transactions in the drift window")
DRIFT_LAST_RUN = Gauge("finguard_drift_last_check_timestamp_seconds", "Unix time of the last drift check")
