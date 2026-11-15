import numpy as np
import pandas as pd

from finguard.drift import build_profile, compute_drift, monitoring_frame, psi


def test_psi_zero_for_identical_distributions():
    p = np.array([0.2, 0.3, 0.5])
    assert psi(p, p) == 0.0
    assert psi(p, np.array([0.5, 0.3, 0.2])) > 0.25


def test_no_drift_on_same_population(processed):
    half = len(processed) // 2
    ref, cur = processed.iloc[:half], processed.iloc[half:]
    report = compute_drift(build_profile(monitoring_frame(ref)), monitoring_frame(cur))
    assert not report["drift_detected"], report["drifted_features"]


def test_drift_detected_on_shifted_population(processed):
    profile = build_profile(monitoring_frame(processed))
    shifted = processed.copy()
    shifted["amount"] *= 3
    shifted["timestamp"] = shifted["timestamp"] + pd.Timedelta(hours=8)
    shifted["channel"] = "online"
    shifted["new_device"] = True
    report = compute_drift(profile, monitoring_frame(shifted))
    assert report["drift_detected"]
    assert {"amount", "hour", "channel", "new_device"} <= set(report["drifted_features"])


def test_zero_inflated_feature_keeps_zero_bin(processed):
    profile = build_profile(monitoring_frame(processed))
    frame = monitoring_frame(processed)
    frame["txn_count_1h"] = 5.0  # everyone suddenly bursting
    report = compute_drift(profile, frame)
    assert report["features"]["txn_count_1h"]["status"] == "significant"


def test_prediction_drift(processed):
    frame = monitoring_frame(processed)
    rng = np.random.default_rng(0)
    profile = build_profile(frame, scores=rng.beta(1, 50, len(frame)))
    report = compute_drift(profile, frame, scores=rng.beta(5, 5, len(frame)))
    assert report["prediction"]["status"] == "significant"
    assert report["drift_detected"]
