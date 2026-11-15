import numpy as np
import pandas as pd

from finguard.features import FEATURE_COLUMNS, build_features


def test_feature_matrix_shape_and_no_nans(processed):
    X = build_features(processed)
    assert list(X.columns) == FEATURE_COLUMNS
    assert not X.isna().any().any()
    assert len(X) == len(processed)


def test_missing_history_is_handled(txn):
    txn = {**txn, "avg_amount_30d": None, "seconds_since_last_txn": None}
    X = build_features(pd.DataFrame([txn]))
    assert X["no_history"].iloc[0] == 1.0
    assert X["amount_to_avg_ratio_log"].iloc[0] == np.log1p(1.0)
    assert not X.isna().any().any()


def test_timezone_aware_timestamp_keeps_local_wall_clock(txn):
    # 02:13 local time in UTC+8 is a night-time purchase for the cardholder.
    X = build_features(pd.DataFrame([txn]))
    assert X["is_night"].iloc[0] == 1.0


def test_one_hot_columns_are_fixed(txn):
    X = build_features(pd.DataFrame([txn]))
    onehot = [c for c in FEATURE_COLUMNS if c.startswith(("channel_", "category_"))]
    assert X[onehot].sum(axis=1).iloc[0] == 2.0
    assert X["category_electronics"].iloc[0] == 1.0
    assert X["channel_online"].iloc[0] == 1.0
