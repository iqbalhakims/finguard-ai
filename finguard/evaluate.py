"""Model evaluation for imbalanced fraud detection.

Accuracy is meaningless at a 1% base rate (predicting "legit" for everything
scores 99%), so the platform reports:

* **PR-AUC** (average precision) — threshold-free ranking quality; the headline metric.
* **Precision / recall / F1** at the operating threshold chosen on validation data.
* **Precision @ 80% recall** — how noisy the alert queue is when catching most fraud.
* **Fraud amount recall** — share of fraudulent *money* caught, which is what the bank loses.
* ROC-AUC for reference only (it is optimistic under heavy imbalance).
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from sklearn.metrics import (  # noqa: E402
    average_precision_score,
    confusion_matrix,
    precision_recall_curve,
    roc_auc_score,
)


def select_threshold(y_true: np.ndarray, scores: np.ndarray) -> float:
    """Threshold that maximises F1 on the given (validation) data."""
    precision, recall, thresholds = precision_recall_curve(y_true, scores)
    f1 = 2 * precision * recall / np.clip(precision + recall, 1e-12, None)
    best = int(np.nanargmax(f1[:-1]))  # last P/R point has no threshold
    return float(thresholds[best])


def threshold_for_precision(y_true: np.ndarray, scores: np.ndarray, target: float, floor: float) -> float:
    """Lowest threshold whose precision reaches ``target`` (used for auto-block).

    Falls back to ``floor`` if the target precision is never reached, and never
    returns a value below ``floor``.
    """
    precision, _, thresholds = precision_recall_curve(y_true, scores)
    ok = np.where(precision[:-1] >= target)[0]
    if len(ok) == 0:
        return float(max(floor, 0.9))
    return float(max(floor, thresholds[ok[0]]))


def precision_at_recall(y_true: np.ndarray, scores: np.ndarray, target_recall: float) -> float:
    precision, recall, _ = precision_recall_curve(y_true, scores)
    ok = recall >= target_recall
    return float(precision[ok].max()) if ok.any() else 0.0


def compute_metrics(
    y_true: np.ndarray,
    scores: np.ndarray,
    threshold: float,
    amounts: np.ndarray | None = None,
) -> dict[str, float]:
    y_true = np.asarray(y_true).astype(int)
    y_pred = (scores >= threshold).astype(int)
    tn, fp, fn, tp = confusion_matrix(y_true, y_pred, labels=[0, 1]).ravel()
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    metrics = {
        "pr_auc": float(average_precision_score(y_true, scores)),
        "roc_auc": float(roc_auc_score(y_true, scores)),
        "precision": float(precision),
        "recall": float(recall),
        "f1": float(2 * precision * recall / (precision + recall)) if precision + recall else 0.0,
        "precision_at_recall_80": precision_at_recall(y_true, scores, 0.80),
        "alert_rate": float(y_pred.mean()),
        "true_positives": int(tp),
        "false_positives": int(fp),
        "false_negatives": int(fn),
        "true_negatives": int(tn),
    }
    if amounts is not None:
        fraud_amount = amounts[y_true == 1].sum()
        caught = amounts[(y_true == 1) & (y_pred == 1)].sum()
        metrics["fraud_amount_recall"] = float(caught / fraud_amount) if fraud_amount else 0.0
    return metrics


def plot_pr_curves(curves: dict[str, tuple[np.ndarray, np.ndarray]], base_rate: float, title: str):
    """``curves`` maps label -> (y_true, scores)."""
    fig, ax = plt.subplots(figsize=(6.5, 5))
    for label, (y_true, scores) in curves.items():
        precision, recall, _ = precision_recall_curve(y_true, scores)
        ap = average_precision_score(y_true, scores)
        ax.plot(recall, precision, label=f"{label} (PR-AUC={ap:.3f})", linewidth=2)
    ax.axhline(base_rate, color="grey", linestyle="--", linewidth=1, label=f"random ({base_rate:.3f})")
    ax.set(xlabel="Recall", ylabel="Precision", title=title, xlim=(0, 1), ylim=(0, 1.02))
    ax.legend(loc="lower left")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    return fig


def plot_confusion(metrics: dict[str, float], title: str):
    cm = np.array(
        [
            [metrics["true_negatives"], metrics["false_positives"]],
            [metrics["false_negatives"], metrics["true_positives"]],
        ]
    )
    fig, ax = plt.subplots(figsize=(4.5, 4))
    ax.imshow(cm, cmap="Blues")
    for (i, j), v in np.ndenumerate(cm):
        ax.text(j, i, f"{v:,}", ha="center", va="center", color="black", fontsize=12)
    ax.set_xticks([0, 1], ["legit", "fraud"])
    ax.set_yticks([0, 1], ["legit", "fraud"])
    ax.set(xlabel="Predicted", ylabel="Actual", title=title)
    fig.tight_layout()
    return fig
