import numpy as np

from finguard.evaluate import compute_metrics, select_threshold, threshold_for_precision


def test_perfect_separation():
    y = np.array([0, 0, 0, 1, 1])
    s = np.array([0.1, 0.2, 0.3, 0.8, 0.9])
    t = select_threshold(y, s)
    m = compute_metrics(y, s, t, amounts=np.array([1, 1, 1, 100, 50]))
    assert m["precision"] == m["recall"] == m["f1"] == 1.0
    assert m["pr_auc"] == 1.0
    assert m["fraud_amount_recall"] == 1.0


def test_metrics_counts():
    y = np.array([0, 0, 1, 1])
    s = np.array([0.9, 0.1, 0.8, 0.2])
    m = compute_metrics(y, s, 0.5)
    assert (m["true_positives"], m["false_positives"], m["false_negatives"], m["true_negatives"]) == (1, 1, 1, 1)
    assert m["precision"] == 0.5 and m["recall"] == 0.5


def test_block_threshold_never_below_review_threshold():
    rng = np.random.default_rng(0)
    y = rng.integers(0, 2, 500)
    s = rng.random(500)
    assert threshold_for_precision(y, s, 0.95, floor=0.4) >= 0.4
