import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

import finguard.api.app as api
from finguard.explain import ShapExplainer
from finguard.features import build_features
from finguard.model import ModelBundle


@pytest.fixture(scope="module")
def client(bundle_dir):
    api.MODEL_PATH = bundle_dir
    with TestClient(api.app) as c:
        yield c


def test_health(client):
    assert client.get("/health/live").json() == {"status": "alive"}
    assert client.get("/health/ready").json()["model_version"] == "test"


def test_predict(client, txn):
    r = client.post("/predict", json=txn)
    assert r.status_code == 200
    body = r.json()
    assert 0.0 <= body["fraud_probability"] <= 1.0
    assert body["decision"] in {"approve", "review", "block"}
    assert body["is_fraud"] == (body["decision"] != "approve")
    assert "explanation" not in body


def test_predict_with_explanation(client, txn):
    body = client.post("/predict?explain=true", json=txn).json()
    contributions = body["explanation"]["contributions"]
    assert len(contributions) == 5
    magnitudes = [abs(c["shap_value"]) for c in contributions]
    assert magnitudes == sorted(magnitudes, reverse=True)


def test_batch(client, txn):
    payload = {"transactions": [txn, {**txn, "transaction_id": "t-2", "amount": 12.0, "new_device": False}]}
    body = client.post("/predict/batch", json=payload).json()
    assert body["count"] == 2
    assert [p["transaction_id"] for p in body["predictions"]] == ["t-1", "t-2"]


def test_validation_errors(client, txn):
    assert client.post("/predict", json={**txn, "amount": -5}).status_code == 422
    assert client.post("/predict", json={**txn, "merchant_category": "casino"}).status_code == 422
    assert client.post("/predict/batch", json={"transactions": []}).status_code == 422


def test_metrics_and_drift_endpoints(client, txn):
    client.post("/predict", json=txn)
    text = client.get("/metrics").text
    assert "finguard_predictions_total" in text
    assert 'finguard_model_info{model_name="finguard-fraud-detector",model_type="xgboost",model_version="test"} 1.0' in text
    assert 'route="/predict"' in text
    assert client.get("/drift").json()["status"] == "warming_up"
    assert client.get("/model").json()["model_type"] == "xgboost"


def test_shap_values_reconstruct_model_output(bundle_dir, processed):
    bundle = ModelBundle.load(bundle_dir)
    X = build_features(processed.head(20))
    expl = ShapExplainer(bundle.estimator, bundle.background).explain(X)
    margin = expl.values.sum(axis=1) + expl.base_values
    proba = 1 / (1 + np.exp(-margin))
    np.testing.assert_allclose(proba, bundle.predict_proba(X), atol=1e-4)


def test_feature_skew_guard(bundle_dir, tmp_path):
    import json, shutil

    shutil.copytree(bundle_dir, tmp_path / "b")
    meta = json.loads((tmp_path / "b" / "metadata.json").read_text())
    meta["features"] = meta["features"][:-1]
    (tmp_path / "b" / "metadata.json").write_text(json.dumps(meta))
    with pytest.raises(RuntimeError, match="feature list"):
        ModelBundle.load(tmp_path / "b")
