"""Transaction processing with pandas: validation, cleaning and behavioural aggregates.

The fraud signal is mostly *relative*: €900 is normal for one customer and
alarming for another; five purchases in ten minutes is unusual for anyone.
This step derives per-customer history features using **only transactions that
happened strictly before the current one** (``closed="left"`` windows), so the
model never sees the future.

In production these aggregates would be served by a feature store (e.g. Feast
backed by Redis) and passed to the inference API with each request; the API
schema mirrors the columns produced here.

Usage::

    python -m finguard.data.process
    python -m finguard.data.process --input data/raw/transactions_drifted.parquet --output ...
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from finguard.config import CHANNELS, MERCHANT_CATEGORIES, PROCESSED_PATH, RAW_PATH

RAW_COLUMNS = [
    "transaction_id",
    "customer_id",
    "timestamp",
    "amount",
    "merchant_category",
    "channel",
    "card_present",
    "is_international",
    "new_device",
    "distance_from_home_km",
]
AGGREGATE_COLUMNS = [
    "txn_count_1h",
    "txn_count_24h",
    "amount_sum_24h",
    "avg_amount_30d",
    "seconds_since_last_txn",
]


def validate_and_clean(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Enforce the raw schema and drop records that cannot be scored."""
    missing = [c for c in RAW_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"missing required columns: {missing}")

    report: dict[str, int] = {"input_rows": len(df)}
    df = df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
    df["distance_from_home_km"] = pd.to_numeric(df["distance_from_home_km"], errors="coerce")
    for col in ("card_present", "is_international", "new_device"):
        df[col] = df[col].astype(bool)

    checks = {
        "duplicate_transaction_id": df["transaction_id"].duplicated(),
        "null_required_field": df[RAW_COLUMNS].isna().any(axis=1),
        "non_positive_amount": ~(df["amount"] > 0),
        "negative_distance": df["distance_from_home_km"] < 0,
        "unknown_merchant_category": ~df["merchant_category"].isin(MERCHANT_CATEGORIES),
        "unknown_channel": ~df["channel"].isin(CHANNELS),
    }
    bad = pd.Series(False, index=df.index)
    for name, mask in checks.items():
        report[name] = int((mask & ~bad).sum())  # count each row once, first failing rule
        bad |= mask
    df = df[~bad]
    report["output_rows"] = len(df)
    return df, report


def add_customer_aggregates(df: pd.DataFrame) -> pd.DataFrame:
    """Add point-in-time behavioural features per customer (no look-ahead)."""
    df = df.sort_values(["customer_id", "timestamp"], kind="stable").reset_index(drop=True)
    grouped = df.groupby("customer_id", sort=False)

    def window(span: str, agg: str) -> pd.Series:
        rolled = grouped.rolling(span, on="timestamp", closed="left")["amount"]
        out = getattr(rolled, agg)()
        # The result is indexed by (customer_id, timestamp), in group order. Because
        # df is sorted by customer then time and groupby(sort=False) keeps the order
        # of appearance, the rows line up positionally with df.
        assert len(out) == len(df)
        return pd.Series(out.to_numpy(), index=df.index)

    df["txn_count_1h"] = window("1h", "count").fillna(0).astype("int32")
    df["txn_count_24h"] = window("24h", "count").fillna(0).astype("int32")
    df["amount_sum_24h"] = window("24h", "sum").fillna(0.0).round(2)
    df["avg_amount_30d"] = window("30D", "mean").round(2)  # NaN = no history yet
    df["seconds_since_last_txn"] = grouped["timestamp"].diff().dt.total_seconds()  # NaN = first txn
    return df.sort_values("timestamp", kind="stable").reset_index(drop=True)


def process(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    df, report = validate_and_clean(df)
    df = add_customer_aggregates(df)
    report.update(
        {
            "customers": int(df["customer_id"].nunique()),
            "fraud_rate": float(df["is_fraud"].mean()) if "is_fraud" in df else None,
            "period_start": str(df["timestamp"].min()),
            "period_end": str(df["timestamp"].max()),
            "first_txn_share": float(df["avg_amount_30d"].isna().mean()),
        }
    )
    return df, report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", type=Path, default=RAW_PATH)
    parser.add_argument("--output", type=Path, default=PROCESSED_PATH)
    args = parser.parse_args()

    raw = pd.read_parquet(args.input)
    df, report = process(raw)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(args.output, index=False)
    args.output.with_suffix(".quality.json").write_text(json.dumps(report, indent=2))

    print(json.dumps(report, indent=2))
    print("\naggregate summary:")
    print(df[AGGREGATE_COLUMNS].describe().T[["mean", "50%", "max"]].round(2).to_string())
    by_label = df.groupby("is_fraud")[["txn_count_1h", "amount_sum_24h"]].mean().round(2)
    print("\nmean by label:\n" + by_label.to_string())
    print(f"\nwritten to {args.output}")


if __name__ == "__main__":
    np.seterr(all="ignore")
    main()
