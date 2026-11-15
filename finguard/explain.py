"""SHAP explainability for both model families.

* XGBoost  -> ``shap.TreeExplainer`` (exact TreeSHAP, fast enough per request)
* Logistic -> ``shap.LinearExplainer`` on the standardised features

SHAP values are in log-odds space for both models: a positive value pushes the
transaction towards "fraud", a negative value towards "legit". Each row's
values sum (with the base value) to the model's raw margin.

Plotting helpers import matplotlib lazily so the serving image stays lean.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import shap
from sklearn.pipeline import Pipeline


class ShapExplainer:
    def __init__(self, estimator: Any, background: pd.DataFrame):
        self.feature_names = list(background.columns)
        if isinstance(estimator, Pipeline):
            self._preprocess = estimator[:-1]
            masker = shap.maskers.Independent(self._transform(background), max_samples=len(background))
            self._explainer = shap.LinearExplainer(estimator[-1], masker)
        else:
            self._preprocess = None
            self._explainer = shap.TreeExplainer(estimator)

    def _transform(self, X: pd.DataFrame) -> pd.DataFrame:
        if self._preprocess is None:
            return X
        return pd.DataFrame(self._preprocess.transform(X), columns=self.feature_names, index=X.index)

    def explain(self, X: pd.DataFrame) -> shap.Explanation:
        """SHAP explanation whose ``data`` holds the *unscaled* feature values."""
        sv = self._explainer(self._transform(X[self.feature_names]))
        values = np.asarray(sv.values)
        if values.ndim == 3:  # some explainers return one slice per class
            values = values[..., 1]
        base = np.broadcast_to(np.asarray(sv.base_values, dtype=float).reshape(-1)[-1], len(X))
        return shap.Explanation(
            values=values,
            base_values=np.array(base),
            data=X[self.feature_names].to_numpy(),
            feature_names=self.feature_names,
        )

    def top_contributions(self, X: pd.DataFrame, k: int = 5) -> list[dict]:
        """Per-row top-k features by |SHAP|, ready to serialise in an API response."""
        expl = self.explain(X)
        results = []
        for i in range(len(X)):
            order = np.argsort(-np.abs(expl.values[i]))[:k]
            results.append(
                {
                    "base_value": float(expl.base_values[i]),
                    "contributions": [
                        {
                            "feature": self.feature_names[j],
                            "value": float(expl.data[i, j]),
                            "shap_value": round(float(expl.values[i, j]), 6),
                            "effect": "increases_risk" if expl.values[i, j] > 0 else "decreases_risk",
                        }
                        for j in order
                    ],
                }
            )
        return results


def global_importance(expl: shap.Explanation) -> dict[str, float]:
    """Mean |SHAP| per feature, sorted descending."""
    mean_abs = np.abs(expl.values).mean(axis=0)
    order = np.argsort(-mean_abs)
    return {expl.feature_names[i]: round(float(mean_abs[i]), 6) for i in order}


def plot_beeswarm(expl: shap.Explanation, title: str, max_display: int = 15):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.figure()
    shap.plots.beeswarm(expl, max_display=max_display, show=False)
    fig = plt.gcf()
    fig.suptitle(title)
    fig.set_size_inches(9, 6.5)
    fig.tight_layout()
    return fig


def plot_importance_bar(expl: shap.Explanation, title: str, max_display: int = 15):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.figure()
    shap.plots.bar(expl, max_display=max_display, show=False)
    fig = plt.gcf()
    fig.suptitle(title)
    fig.set_size_inches(8, 6)
    fig.tight_layout()
    return fig
