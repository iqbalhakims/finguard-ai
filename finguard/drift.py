"""Data and prediction drift detection.

Method
------
* **PSI (Population Stability Index)** per monitored feature, comparing the
  live distribution against a reference profile captured at training time.
  Numeric features use reference-quantile bins; categorical features use their
  category shares. Standard banking interpretation::

      PSI < 0.10        stable
      0.10 - 0.25       moderate shift, investigate
      >= 0.25           significant shift

* **Prediction drift**: PSI of the model's fraud-score distribution.
* **Kolmogorov-Smirnov** test on numeric features (offline reports only,
  because it needs raw reference samples rather than a compact profile).

A feature counts as *drifted* at PSI >= 0.10 (the common convention, also used
by Evidently's PSI test). **Dataset drift** is declared when at least
``drift_share`` (default 30%) of monitored features drifted, or when the
model's score distribution itself shifts significantly (PSI >= 0.25).

The same ``compute_drift`` function is used by the online monitor inside the
API (rolling window -> Prometheus gauges) and by this offline CLI::

    python -m finguard.drift --current data/raw/transactions_drifted.parquet
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from finguard.features import wall_clock

NUMERIC = ["amount", "amount_to_avg_ratio", "distance_from_home_km", "txn_count_1h", "txn_count_24h", "hour"]
CATEGORICAL = ["merchant_category", "channel", "is_international", "card_present", "new_device"]
MONITORED = NUMERIC + CATEGORICAL
SCORE = "fraud_score"

PSI_MODERATE = 0.10
PSI_SIGNIFICANT = 0.25
EPS = 1e-4


def monitoring_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Interpretable, business-level view of the inputs used for drift monitoring."""
    ts = wall_clock(df["timestamp"])
    avg = df["avg_amount_30d"].astype(float)
    amount = df["amount"].astype(float)
    ratio = np.where(avg.isna() | (avg <= 0), 1.0, amount / avg.where(avg > 0, 1.0))
    return pd.DataFrame(
        {
            "amount": amount,
            "amount_to_avg_ratio": ratio,
            "distance_from_home_km": df["distance_from_home_km"].astype(float),
            "txn_count_1h": df["txn_count_1h"].astype(float),
            "txn_count_24h": df["txn_count_24h"].astype(float),
            "hour": (ts.dt.hour + ts.dt.minute / 60.0).to_numpy(),
            "merchant_category": df["merchant_category"].astype(str),
            "channel": df["channel"].astype(str),
            "is_international": df["is_international"].astype(bool).astype(str),
            "card_present": df["card_present"].astype(bool).astype(str),
            "new_device": df["new_device"].astype(bool).astype(str),
        },
        index=df.index,
    )


# --------------------------------------------------------------------------- profile


def _numeric_bins(values: np.ndarray, edges: np.ndarray) -> np.ndarray:
    # side="left" -> bins are (e[i-1], e[i]]; keeps zero-inflated counts (e.g.
    # txn_count_1h, mostly 0) in their own bin.
    counts = np.bincount(np.searchsorted(edges, values, side="left"), minlength=len(edges) + 1)
    return counts / max(counts.sum(), 1)


def _numeric_profile(values: np.ndarray, n_bins: int) -> dict:
    values = values[~np.isnan(values)]
    edges = np.unique(np.quantile(values, np.linspace(0, 1, n_bins + 1)[1:-1]))
    return {
        "type": "numeric",
        "edges": edges.tolist(),
        "proportions": _numeric_bins(values, edges).tolist(),
        "mean": float(values.mean()),
        "p50": float(np.median(values)),
    }


def _categorical_profile(values: pd.Series) -> dict:
    shares = values.astype(str).value_counts(normalize=True)
    return {"type": "categorical", "proportions": {k: float(v) for k, v in shares.items()}}


def build_profile(frame: pd.DataFrame, scores: np.ndarray | None = None, n_bins: int = 10) -> dict:
    profile = {"n_reference": len(frame), "features": {}}
    for col in NUMERIC:
        profile["features"][col] = _numeric_profile(frame[col].to_numpy(dtype=float), n_bins)
    for col in CATEGORICAL:
        profile["features"][col] = _categorical_profile(frame[col])
    if scores is not None:
        profile["prediction"] = _numeric_profile(np.asarray(scores, dtype=float), n_bins)
    return profile


# --------------------------------------------------------------------------- PSI


def psi(expected: np.ndarray, actual: np.ndarray) -> float:
    e = np.clip(np.asarray(expected, dtype=float), EPS, None)
    a = np.clip(np.asarray(actual, dtype=float), EPS, None)
    return float(np.sum((a - e) * np.log(a / e)))


def _status(value: float) -> str:
    if value >= PSI_SIGNIFICANT:
        return "significant"
    if value >= PSI_MODERATE:
        return "moderate"
    return "stable"


def _numeric_psi(spec: dict, values: np.ndarray) -> tuple[float, dict]:
    values = values[~np.isnan(values)]
    actual = _numeric_bins(values, np.asarray(spec["edges"]))
    return psi(spec["proportions"], actual), {
        "reference_mean": spec["mean"],
        "current_mean": float(values.mean()) if len(values) else None,
    }


def _categorical_psi(spec: dict, values: pd.Series) -> tuple[float, dict]:
    ref = spec["proportions"]
    cur = values.astype(str).value_counts(normalize=True).to_dict()
    cats = sorted(set(ref) | set(cur))
    expected = [ref.get(c, 0.0) for c in cats]
    actual = [cur.get(c, 0.0) for c in cats]
    top_shift = max(cats, key=lambda c: abs(cur.get(c, 0.0) - ref.get(c, 0.0)))
    return psi(expected, actual), {
        "largest_shift": top_shift,
        "reference_share": round(ref.get(top_shift, 0.0), 4),
        "current_share": round(cur.get(top_shift, 0.0), 4),
    }


def compute_drift(
    profile: dict,
    frame: pd.DataFrame,
    scores: np.ndarray | None = None,
    drift_share: float = 0.3,
) -> dict:
    features = {}
    for name, spec in profile["features"].items():
        if name not in frame:
            continue
        if spec["type"] == "numeric":
            value, detail = _numeric_psi(spec, frame[name].to_numpy(dtype=float))
        else:
            value, detail = _categorical_psi(spec, frame[name])
        features[name] = {"psi": round(value, 4), "status": _status(value), **detail}

    report: dict = {"n_samples": len(frame), "features": features}
    drifted = [n for n, f in features.items() if f["psi"] >= PSI_MODERATE]
    report["drifted_features"] = drifted
    report["drift_share"] = round(len(drifted) / max(len(features), 1), 4)

    prediction_drift = False
    if scores is not None and "prediction" in profile:
        value, detail = _numeric_psi(profile["prediction"], np.asarray(scores, dtype=float))
        report["prediction"] = {"psi": round(value, 4), "status": _status(value), **detail}
        prediction_drift = value >= PSI_SIGNIFICANT

    report["drift_detected"] = bool(report["drift_share"] >= drift_share or prediction_drift)
    return report


def ks_tests(reference: pd.DataFrame, current: pd.DataFrame, alpha: float = 0.01) -> dict:
    from scipy.stats import ks_2samp

    out = {}
    for col in NUMERIC:
        res = ks_2samp(reference[col].dropna(), current[col].dropna())
        out[col] = {
            "statistic": round(float(res.statistic), 4),
            "p_value": float(res.pvalue),
            "drift": bool(res.pvalue < alpha),
        }
    return out


# --------------------------------------------------------------------------- CLI


def _print_report(report: dict) -> None:
    print(f"\n{'feature':<24}{'PSI':>8}  status        detail")
    print("-" * 78)
    rows = list(report["features"].items())
    if "prediction" in report:
        rows.append((SCORE, report["prediction"]))
    for name, f in rows:
        if "largest_shift" in f:
            detail = f"{f['largest_shift']}: {f['reference_share']:.1%} -> {f['current_share']:.1%}"
        else:
            cur = f["current_mean"]
            detail = f"mean {f['reference_mean']:.3g} -> {cur:.3g}" if cur is not None else ""
        print(f"{name:<24}{f['psi']:>8.3f}  {f['status']:<12}  {detail}")
    print("-" * 78)
    print(f"drifted features : {report['drifted_features'] or 'none'}")
    print(f"DRIFT DETECTED   : {report['drift_detected']}")


def main() -> None:
    from finguard.config import (
        MLFLOW_DRIFT_EXPERIMENT,
        MODEL_DIR,
        PROCESSED_PATH,
        RAW_DRIFTED_PATH,
        REPORTS_DIR,
    )
    from finguard.data.process import process
    from finguard.evaluate import compute_metrics
    from finguard.features import build_features
    from finguard.model import ModelBundle

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--current", type=Path, default=RAW_DRIFTED_PATH, help="raw transactions to check")
    parser.add_argument("--reference", type=Path, default=PROCESSED_PATH, help="processed training data (for KS)")
    parser.add_argument("--model-dir", type=Path, default=MODEL_DIR)
    parser.add_argument("--output", type=Path, default=REPORTS_DIR / "drift_report.json")
    parser.add_argument("--no-mlflow", action="store_true")
    args = parser.parse_args()

    bundle = ModelBundle.load(args.model_dir)
    current, quality = process(pd.read_parquet(args.current))
    scores = bundle.predict_proba(build_features(current))
    cur_frame = monitoring_frame(current)

    report = compute_drift(bundle.drift_profile, cur_frame, scores)
    report["source"] = str(args.current)
    report["model_version"] = bundle.version
    if args.reference.exists():
        reference = pd.read_parquet(args.reference)
        ref_frame = monitoring_frame(reference.sample(min(len(reference), 50_000), random_state=0))
        report["ks_tests"] = ks_tests(ref_frame, cur_frame.sample(min(len(cur_frame), 50_000), random_state=0))
    if "is_fraud" in current:  # labels available (e.g. after chargebacks arrive)
        report["performance"] = compute_metrics(
            current["is_fraud"].to_numpy(), scores, bundle.threshold, current["amount"].to_numpy()
        )
        report["performance"]["reference_pr_auc"] = bundle.metadata["metrics"]["test"]["pr_auc"]

    _print_report(report)
    if "performance" in report:
        p = report["performance"]
        print(
            f"performance      : PR-AUC {p['pr_auc']:.3f} (reference {p['reference_pr_auc']:.3f}), "
            f"precision {p['precision']:.3f}, recall {p['recall']:.3f}"
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, default=str))
    print(f"report written   : {args.output}")

    if not args.no_mlflow:
        import mlflow

        from finguard.tracking import setup_mlflow

        setup_mlflow(MLFLOW_DRIFT_EXPERIMENT)
        with mlflow.start_run(run_name=f"drift-check-{args.current.stem}"):
            mlflow.set_tags({"model_version": bundle.version, "source": str(args.current)})
            mlflow.log_metrics({f"psi_{k}": v["psi"] for k, v in report["features"].items()})
            if "prediction" in report:
                mlflow.log_metric("psi_fraud_score", report["prediction"]["psi"])
            mlflow.log_metric("drift_detected", int(report["drift_detected"]))
            mlflow.log_metric("drift_share", report["drift_share"])
            if "performance" in report:
                mlflow.log_metrics({f"current_{k}": v for k, v in report["performance"].items()})
            mlflow.log_dict(report, "drift_report.json")
            mlflow.log_dict(quality, "data_quality.json")
        print(f"logged to MLflow : experiment '{MLFLOW_DRIFT_EXPERIMENT}'")


if __name__ == "__main__":
    main()
