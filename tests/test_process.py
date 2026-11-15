import numpy as np
import pandas as pd
import pytest

from finguard.data.process import add_customer_aggregates, validate_and_clean


def _row(txn_id, cust, ts, amount, **kw):
    return {
        "transaction_id": txn_id,
        "customer_id": cust,
        "timestamp": pd.Timestamp(ts),
        "amount": amount,
        "merchant_category": kw.get("merchant_category", "grocery"),
        "channel": kw.get("channel", "pos"),
        "card_present": True,
        "is_international": False,
        "new_device": False,
        "distance_from_home_km": 1.0,
        "is_fraud": 0,
    }


def test_aggregates_use_only_prior_transactions():
    df = pd.DataFrame(
        [
            _row("1", "a", "2026-01-01 10:00", 10.0),
            _row("2", "b", "2026-01-01 10:10", 999.0),  # other customer must not leak in
            _row("3", "a", "2026-01-01 10:30", 20.0),
            _row("4", "a", "2026-01-01 12:00", 30.0),
        ]
    )
    out = add_customer_aggregates(df).set_index("transaction_id")
    a = out.loc[["1", "3", "4"]]
    assert a["txn_count_1h"].tolist() == [0, 1, 0]
    assert a["txn_count_24h"].tolist() == [0, 1, 2]
    assert a["amount_sum_24h"].tolist() == [0.0, 10.0, 30.0]
    assert np.isnan(a["avg_amount_30d"].iloc[0])
    assert a["avg_amount_30d"].iloc[1:].tolist() == [10.0, 15.0]
    assert np.isnan(a["seconds_since_last_txn"].iloc[0])
    assert a["seconds_since_last_txn"].iloc[1:].tolist() == [1800.0, 5400.0]
    assert out.loc["2", "txn_count_24h"] == 0


def test_output_is_time_ordered():
    df = pd.DataFrame([_row("1", "b", "2026-01-02", 5), _row("2", "a", "2026-01-01", 5)])
    out = add_customer_aggregates(df)
    assert out["timestamp"].is_monotonic_increasing


def test_validation_drops_bad_rows():
    df = pd.DataFrame(
        [
            _row("1", "a", "2026-01-01", 10.0),
            _row("1", "a", "2026-01-01", 10.0),  # duplicate id
            _row("2", "a", "2026-01-01", -1.0),  # negative amount
            _row("3", "a", "2026-01-01", 5.0, merchant_category="casino"),
            _row("4", "a", "2026-01-01", 5.0, channel="fax"),
        ]
    )
    clean, report = validate_and_clean(df)
    assert clean["transaction_id"].tolist() == ["1"]
    assert report["duplicate_transaction_id"] == 1
    assert report["non_positive_amount"] == 1
    assert report["unknown_merchant_category"] == 1
    assert report["unknown_channel"] == 1


def test_missing_column_raises():
    with pytest.raises(ValueError, match="missing required columns"):
        validate_and_clean(pd.DataFrame({"transaction_id": ["1"]}))
