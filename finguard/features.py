"""Model feature engineering shared by training and serving.

``build_features`` is the single source of truth for turning a transaction
record (raw fields + customer aggregates) into the model's feature matrix. The
training pipeline and the FastAPI service both call it, which removes the most
common cause of production ML bugs: train/serve skew.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from finguard.config import CHANNELS, MERCHANT_CATEGORIES

THIRTY_DAYS_S = 30 * 86_400.0

NUMERIC_FEATURES = [
    "amount_log",
    "amount_to_avg_ratio_log",
    "amount_sum_24h_log",
    "txn_count_1h",
    "txn_count_24h",
    "seconds_since_last_txn_log",
    "distance_from_home_log",
    "hour_sin",
    "hour_cos",
    "is_night",
    "is_weekend",
    "no_history",
    "is_international",
    "card_present",
    "new_device",
]
FEATURE_COLUMNS = (
    NUMERIC_FEATURES
    + [f"channel_{c}" for c in CHANNELS]
    + [f"category_{c}" for c in MERCHANT_CATEGORIES]
)


def wall_clock(ts: pd.Series) -> pd.Series:
    """Parse timestamps and keep the local wall-clock time (drop any tz info).

    Behavioural features such as "night-time purchase" are about the
    cardholder's local clock, so timezone-aware inputs keep their local
    reading rather than being converted to UTC.
    """
    ts = pd.to_datetime(ts)
    if getattr(ts.dt, "tz", None) is not None:
        ts = ts.dt.tz_localize(None)
    return ts


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Return the model feature matrix (columns == ``FEATURE_COLUMNS``)."""
    ts = wall_clock(df["timestamp"])
    hour = ts.dt.hour + ts.dt.minute / 60.0
    amount = df["amount"].astype(float)
    avg_30d = df["avg_amount_30d"].astype(float)
    no_history = avg_30d.isna() | (avg_30d <= 0)
    ratio = np.where(no_history, 1.0, amount / avg_30d.where(~no_history, 1.0))

    out = pd.DataFrame(index=df.index)
    out["amount_log"] = np.log1p(amount)
    out["amount_to_avg_ratio_log"] = np.log1p(np.clip(ratio, 0, 1000))
    out["amount_sum_24h_log"] = np.log1p(df["amount_sum_24h"].astype(float).clip(lower=0))
    out["txn_count_1h"] = df["txn_count_1h"].astype(float).clip(0, 50)
    out["txn_count_24h"] = df["txn_count_24h"].astype(float).clip(0, 200)
    out["seconds_since_last_txn_log"] = np.log1p(
        df["seconds_since_last_txn"].astype(float).fillna(THIRTY_DAYS_S).clip(0, THIRTY_DAYS_S)
    )
    out["distance_from_home_log"] = np.log1p(df["distance_from_home_km"].astype(float).clip(lower=0))
    out["hour_sin"] = np.sin(2 * np.pi * hour / 24)
    out["hour_cos"] = np.cos(2 * np.pi * hour / 24)
    out["is_night"] = (hour < 6).astype(float)
    out["is_weekend"] = (ts.dt.day_of_week >= 5).astype(float)
    out["no_history"] = no_history.astype(float)
    for col in ("is_international", "card_present", "new_device"):
        out[col] = df[col].astype(bool).astype(float)
    for c in CHANNELS:
        out[f"channel_{c}"] = (df["channel"] == c).astype(float)
    for c in MERCHANT_CATEGORIES:
        out[f"category_{c}"] = (df["merchant_category"] == c).astype(float)
    return out[FEATURE_COLUMNS]
