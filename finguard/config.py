"""Central configuration: paths, constants and the shared category vocabulary."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(os.getenv("FINGUARD_HOME", Path(__file__).resolve().parent.parent))

DATA_DIR = ROOT / "data"
RAW_PATH = DATA_DIR / "raw" / "transactions.parquet"
RAW_DRIFTED_PATH = DATA_DIR / "raw" / "transactions_drifted.parquet"
PROCESSED_PATH = DATA_DIR / "processed" / "transactions.parquet"
REPORTS_DIR = ROOT / "reports"

MODEL_DIR = Path(os.getenv("FINGUARD_MODEL_DIR", ROOT / "artifacts" / "model"))

MLFLOW_TRACKING_URI = os.getenv("MLFLOW_TRACKING_URI", f"sqlite:///{ROOT / 'mlflow.db'}")
MLFLOW_ARTIFACT_ROOT = ROOT / "mlartifacts"
MLFLOW_EXPERIMENT = os.getenv("MLFLOW_EXPERIMENT", "finguard-fraud")
MLFLOW_DRIFT_EXPERIMENT = os.getenv("MLFLOW_DRIFT_EXPERIMENT", "finguard-drift")
REGISTERED_MODEL_NAME = os.getenv("REGISTERED_MODEL_NAME", "finguard-fraud-detector")

SEED = 42

# Fixed vocabularies. One-hot columns are generated from these lists (not from
# whatever values happen to appear in a batch), so training and serving always
# produce an identical feature matrix layout.
MERCHANT_CATEGORIES: list[str] = [
    "grocery",
    "restaurant",
    "fuel",
    "retail",
    "online_retail",
    "electronics",
    "travel",
    "entertainment",
    "utilities",
    "health",
    "digital_goods",
    "cash_withdrawal",
]
CHANNELS: list[str] = ["pos", "online", "atm"]
