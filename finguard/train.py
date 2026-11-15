"""Training pipeline: Logistic Regression vs XGBoost, tracked in MLflow.

Steps
-----
1. Load the processed transaction log and split it **by time**
   (train -> validation -> test), as a production model always scores the future.
2. Build features with the shared ``build_features`` (no train/serve skew).
3. Train both candidates. Each one is a nested MLflow run with params, metrics,
   PR curve, confusion matrix, SHAP plots and the logged model.
4. Choose the operating threshold on validation (max F1) plus a stricter
   auto-block threshold (>=95% precision); report metrics on the untouched test set.
5. Promote the model with the best validation PR-AUC: register it in the MLflow
   Model Registry under the ``champion`` alias and export the serving bundle.

Imbalance is handled with threshold tuning rather than class re-weighting so
that the returned scores remain usable as probabilities.

Usage::

    python -m finguard.train
    MLFLOW_TRACKING_URI=http://localhost:5001 python -m finguard.train   # log to the compose server
"""

from __future__ import annotations

import argparse
import time
import warnings
from datetime import UTC, datetime
from pathlib import Path

import matplotlib.pyplot as plt
import mlflow
import mlflow.sklearn
import mlflow.xgboost
import numpy as np
import pandas as pd
from mlflow import MlflowClient
from mlflow.models import infer_signature
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from xgboost import XGBClassifier

from finguard import __version__
from finguard.config import (
    MLFLOW_EXPERIMENT,
    MLFLOW_TRACKING_URI,
    MODEL_DIR,
    PROCESSED_PATH,
    REGISTERED_MODEL_NAME,
    SEED,
)
from finguard.drift import build_profile, monitoring_frame
from finguard.evaluate import (
    compute_metrics,
    plot_confusion,
    plot_pr_curves,
    select_threshold,
    threshold_for_precision,
)
from finguard.explain import ShapExplainer, global_importance, plot_beeswarm, plot_importance_bar
from finguard.features import FEATURE_COLUMNS, build_features
from finguard.model import ModelBundle
from finguard.tracking import setup_mlflow

BLOCK_PRECISION_TARGET = 0.95

warnings.filterwarnings("ignore", message=".*Inferred schema contains integer column.*")
warnings.filterwarnings("ignore", message=".*dataset source can be interpreted in multiple ways.*")


def time_split(df: pd.DataFrame, val_frac: float = 0.15, test_frac: float = 0.15):
    df = df.sort_values("timestamp", kind="stable")
    t = df["timestamp"]
    t_val = t.quantile(1 - val_frac - test_frac)
    t_test = t.quantile(1 - test_frac)
    return df[t < t_val], df[(t >= t_val) & (t < t_test)], df[t >= t_test]


def candidates() -> dict[str, tuple[object, dict]]:
    lr_params = {"C": 0.5, "max_iter": 2000, "solver": "lbfgs"}
    xgb_params = {
        "n_estimators": 800,
        "learning_rate": 0.05,
        "max_depth": 5,
        "min_child_weight": 3,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "reg_lambda": 1.0,
        "tree_method": "hist",
        "eval_metric": "aucpr",
        "early_stopping_rounds": 50,
    }
    return {
        "logistic_regression": (
            Pipeline([("scaler", StandardScaler()), ("clf", LogisticRegression(**lr_params, random_state=SEED))]),
            lr_params,
        ),
        "xgboost": (XGBClassifier(**xgb_params, random_state=SEED, n_jobs=-1), xgb_params),
    }


def fit(name: str, estimator, X_tr, y_tr, X_val, y_val):
    if name == "xgboost":
        estimator.fit(X_tr, y_tr, eval_set=[(X_val, y_val)], verbose=False)
    else:
        estimator.fit(X_tr, y_tr)
    return estimator


def make_bundle(
    estimator,
    *,
    model_type: str,
    model_version: str,
    threshold: float,
    block_threshold: float,
    reference_df: pd.DataFrame,
    reference_scores: np.ndarray,
    background: pd.DataFrame,
    metrics: dict | None = None,
    extra: dict | None = None,
) -> ModelBundle:
    metadata = {
        "model_name": REGISTERED_MODEL_NAME,
        "model_version": model_version,
        "model_type": model_type,
        "threshold": threshold,
        "block_threshold": block_threshold,
        "features": FEATURE_COLUMNS,
        "metrics": metrics or {},
        "trained_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "finguard_version": __version__,
        **(extra or {}),
    }
    return ModelBundle(
        estimator=estimator,
        metadata=metadata,
        drift_profile=build_profile(monitoring_frame(reference_df), reference_scores),
        background=background,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", type=Path, default=PROCESSED_PATH)
    parser.add_argument("--model-dir", type=Path, default=MODEL_DIR)
    parser.add_argument("--shap-sample", type=int, default=2000)
    args = parser.parse_args()

    df = pd.read_parquet(args.data)
    train, val, test = time_split(df)
    X_tr, X_val, X_te = (build_features(d) for d in (train, val, test))
    y_tr, y_val, y_te = (d["is_fraud"].to_numpy() for d in (train, val, test))
    rng = np.random.default_rng(SEED)
    background = X_tr.iloc[rng.choice(len(X_tr), 200, replace=False)].reset_index(drop=True)
    shap_rows = X_te.iloc[rng.choice(len(X_te), min(args.shap_sample, len(X_te)), replace=False)]

    print(f"train {len(train):>7,} rows  fraud {y_tr.mean():.3%}  {train.timestamp.min():%Y-%m-%d} -> {train.timestamp.max():%Y-%m-%d}")
    print(f"val   {len(val):>7,} rows  fraud {y_val.mean():.3%}  {val.timestamp.min():%Y-%m-%d} -> {val.timestamp.max():%Y-%m-%d}")
    print(f"test  {len(test):>7,} rows  fraud {y_te.mean():.3%}  {test.timestamp.min():%Y-%m-%d} -> {test.timestamp.max():%Y-%m-%d}")

    setup_mlflow(MLFLOW_EXPERIMENT)
    results: dict[str, dict] = {}
    with mlflow.start_run(run_name=f"training-pipeline-{datetime.now():%Y%m%d-%H%M%S}") as parent:
        mlflow.set_tags({"pipeline": "fraud-training", "finguard_version": __version__})
        mlflow.log_params(
            {
                "data_path": str(args.data),
                "split": "time-based 70/15/15",
                "n_features": len(FEATURE_COLUMNS),
                "train_rows": len(train),
                "val_rows": len(val),
                "test_rows": len(test),
                "train_fraud_rate": round(float(y_tr.mean()), 5),
                "train_period": f"{train.timestamp.min():%Y-%m-%d}..{train.timestamp.max():%Y-%m-%d}",
                "test_period": f"{test.timestamp.min():%Y-%m-%d}..{test.timestamp.max():%Y-%m-%d}",
            }
        )
        mlflow.log_input(
            mlflow.data.from_pandas(train.drop(columns=["fraud_type"]), source=str(args.data), name="transactions-train", targets="is_fraud"),
            context="training",
        )

        for name, (estimator, params) in candidates().items():
            with mlflow.start_run(run_name=name, nested=True) as run:
                t0 = time.perf_counter()
                fit(name, estimator, X_tr, y_tr, X_val, y_val)
                train_seconds = time.perf_counter() - t0

                val_scores = estimator.predict_proba(X_val)[:, 1]
                test_scores = estimator.predict_proba(X_te)[:, 1]
                threshold = select_threshold(y_val, val_scores)
                block = threshold_for_precision(y_val, val_scores, BLOCK_PRECISION_TARGET, floor=threshold)
                val_m = compute_metrics(y_val, val_scores, threshold, val["amount"].to_numpy())
                test_m = compute_metrics(y_te, test_scores, threshold, test["amount"].to_numpy())

                mlflow.set_tag("model_type", name)
                mlflow.log_params(params)
                if name == "xgboost":
                    mlflow.log_param("best_iteration", estimator.best_iteration)
                mlflow.log_metrics({"threshold": threshold, "block_threshold": block, "train_seconds": train_seconds})
                mlflow.log_metrics({f"val_{k}": v for k, v in val_m.items()})
                mlflow.log_metrics({f"test_{k}": v for k, v in test_m.items()})

                fig = plot_pr_curves({"validation": (y_val, val_scores), "test": (y_te, test_scores)}, y_te.mean(), f"{name} — precision/recall")
                mlflow.log_figure(fig, "plots/pr_curve.png")
                fig = plot_confusion(test_m, f"{name} — test @ {threshold:.3f}")
                mlflow.log_figure(fig, "plots/confusion_matrix_test.png")

                explainer = ShapExplainer(estimator, background)
                expl = explainer.explain(shap_rows)
                importance = global_importance(expl)
                mlflow.log_figure(plot_beeswarm(expl, f"{name} — SHAP summary"), "shap/beeswarm.png")
                mlflow.log_figure(plot_importance_bar(expl, f"{name} — mean |SHAP|"), "shap/importance_bar.png")
                mlflow.log_dict(importance, "shap/global_importance.json")
                plt.close("all")

                signature = infer_signature(X_val.head(200), val_scores[:200])
                example = X_val.head(3)
                if name == "xgboost":
                    info = mlflow.xgboost.log_model(estimator, name="model", signature=signature, input_example=example)
                else:
                    info = mlflow.sklearn.log_model(estimator, name="model", signature=signature, input_example=example)

                results[name] = {
                    "estimator": estimator,
                    "run_id": run.info.run_id,
                    "model_uri": info.model_uri,
                    "threshold": threshold,
                    "block_threshold": block,
                    "val": val_m,
                    "test": test_m,
                    "importance": importance,
                    "val_scores": val_scores,
                    "test_scores": test_scores,
                }
                print(
                    f"\n[{name}] val PR-AUC {val_m['pr_auc']:.4f} | test PR-AUC {test_m['pr_auc']:.4f} "
                    f"precision {test_m['precision']:.3f} recall {test_m['recall']:.3f} "
                    f"@ threshold {threshold:.3f} ({train_seconds:.1f}s)"
                )

        # ---------------------------------------------------------------- compare & promote
        comparison = pd.DataFrame(
            {
                name: {
                    **{f"val_{k}": r["val"][k] for k in ("pr_auc", "precision", "recall", "f1")},
                    **{f"test_{k}": r["test"][k] for k in ("pr_auc", "roc_auc", "precision", "recall", "f1", "precision_at_recall_80", "fraud_amount_recall")},
                    "threshold": r["threshold"],
                }
                for name, r in results.items()
            }
        ).T.round(4)
        champion_name = max(results, key=lambda n: results[n]["val"]["pr_auc"])
        champ = results[champion_name]

        mlflow.log_table(comparison.reset_index(names="model"), "model_comparison.json")
        fig = plot_pr_curves({n: (y_te, r["test_scores"]) for n, r in results.items()}, y_te.mean(), "Test set — model comparison")
        mlflow.log_figure(fig, "plots/pr_curve_comparison.png")
        plt.close("all")
        mlflow.set_tag("champion", champion_name)
        mlflow.log_metric("champion_test_pr_auc", champ["test"]["pr_auc"])

        client = MlflowClient()
        version = mlflow.register_model(champ["model_uri"], REGISTERED_MODEL_NAME)
        client.set_registered_model_alias(REGISTERED_MODEL_NAME, "champion", version.version)
        client.update_registered_model(
            REGISTERED_MODEL_NAME, description="Card-transaction fraud classifier (FinGuard AI). Alias 'champion' = serving model."
        )
        for key, value in {
            "model_type": champion_name,
            "val_pr_auc": f"{champ['val']['pr_auc']:.4f}",
            "test_pr_auc": f"{champ['test']['pr_auc']:.4f}",
            "threshold": f"{champ['threshold']:.4f}",
        }.items():
            client.set_model_version_tag(REGISTERED_MODEL_NAME, version.version, key, value)

        bundle = make_bundle(
            champ["estimator"],
            model_type=champion_name,
            model_version=str(version.version),
            threshold=champ["threshold"],
            block_threshold=champ["block_threshold"],
            reference_df=val,
            reference_scores=champ["val_scores"],
            background=background,
            metrics={"validation": champ["val"], "test": champ["test"]},
            extra={
                "mlflow_run_id": champ["run_id"],
                "mlflow_parent_run_id": parent.info.run_id,
                "mlflow_model_uri": champ["model_uri"],
                "registry_uri": f"models:/{REGISTERED_MODEL_NAME}@champion",
                "shap_global_importance": dict(list(champ["importance"].items())[:10]),
                "train_period": f"{train.timestamp.min():%Y-%m-%d}..{train.timestamp.max():%Y-%m-%d}",
            },
        )
        bundle.save(args.model_dir)
        mlflow.log_artifacts(str(args.model_dir), "serving_bundle")

    print("\n" + comparison.to_string())
    print(f"\nchampion        : {champion_name}  (registered {REGISTERED_MODEL_NAME} v{version.version} @champion)")
    print(f"threshold       : review >= {champ['threshold']:.4f}, block >= {champ['block_threshold']:.4f}")
    print(f"top SHAP drivers: {', '.join(list(champ['importance'])[:5])}")
    print(f"serving bundle  : {args.model_dir}")
    print(f"MLflow          : {MLFLOW_TRACKING_URI}  (run `make mlflow-ui`)")


if __name__ == "__main__":
    main()
