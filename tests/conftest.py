import numpy as np
import pandas as pd
import pytest
from xgboost import XGBClassifier

from finguard.data.generate import generate_transactions
from finguard.data.process import process
from finguard.features import build_features
from finguard.train import make_bundle, time_split


@pytest.fixture(scope="session")
def processed() -> pd.DataFrame:
    raw = generate_transactions(n_customers=400, days=45, seed=7)
    df, _ = process(raw)
    return df


@pytest.fixture(scope="session")
def bundle_dir(tmp_path_factory, processed):
    train, val, _ = time_split(processed)
    X_tr, X_val = build_features(train), build_features(val)
    model = XGBClassifier(n_estimators=60, max_depth=4, learning_rate=0.1, random_state=0)
    model.fit(X_tr, train["is_fraud"])
    scores = model.predict_proba(X_val)[:, 1]
    bundle = make_bundle(
        model,
        model_type="xgboost",
        model_version="test",
        threshold=0.5,
        block_threshold=0.8,
        reference_df=val,
        reference_scores=scores,
        background=X_tr.sample(50, random_state=0).reset_index(drop=True),
        metrics={"test": {"pr_auc": 0.0}},
    )
    path = tmp_path_factory.mktemp("bundle")
    bundle.save(path)
    return path


@pytest.fixture
def txn() -> dict:
    return {
        "transaction_id": "t-1",
        "customer_id": "c-1",
        "timestamp": "2026-11-14T02:13:00+08:00",
        "amount": 1249.99,
        "merchant_category": "electronics",
        "channel": "online",
        "card_present": False,
        "is_international": True,
        "new_device": True,
        "distance_from_home_km": 6800.0,
        "txn_count_1h": 3,
        "txn_count_24h": 4,
        "amount_sum_24h": 310.5,
        "avg_amount_30d": 58.2,
        "seconds_since_last_txn": 420,
    }


