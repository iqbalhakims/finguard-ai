"""Model bundle: everything the inference service needs, in one directory.

    artifacts/model/
      model.joblib          fitted estimator (sklearn Pipeline or XGBClassifier)
      metadata.json         name, version, thresholds, feature list, metrics, lineage
      drift_profile.json    reference distributions for online drift detection
      shap_background.csv   background sample for SHAP (needed by LinearExplainer)

The training pipeline exports the MLflow champion into this format; the Docker
image bakes it in, so a container image == one immutable model version.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd

from finguard.features import FEATURE_COLUMNS


@dataclass
class ModelBundle:
    estimator: Any
    metadata: dict
    drift_profile: dict
    background: pd.DataFrame

    @property
    def name(self) -> str:
        return self.metadata["model_name"]

    @property
    def version(self) -> str:
        return str(self.metadata["model_version"])

    @property
    def model_type(self) -> str:
        return self.metadata["model_type"]

    @property
    def threshold(self) -> float:
        return float(self.metadata["threshold"])

    @property
    def block_threshold(self) -> float:
        return float(self.metadata["block_threshold"])

    def predict_proba(self, features: pd.DataFrame) -> np.ndarray:
        return self.estimator.predict_proba(features[FEATURE_COLUMNS])[:, 1]

    def decide(self, score: float) -> str:
        """approve -> pass; review -> analyst queue; block -> decline in real time."""
        if score >= self.block_threshold:
            return "block"
        if score >= self.threshold:
            return "review"
        return "approve"

    def save(self, path: Path) -> None:
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        joblib.dump(self.estimator, path / "model.joblib")
        (path / "metadata.json").write_text(json.dumps(self.metadata, indent=2, default=str))
        (path / "drift_profile.json").write_text(json.dumps(self.drift_profile, indent=2))
        self.background.to_csv(path / "shap_background.csv", index=False)

    @classmethod
    def load(cls, path: Path) -> ModelBundle:
        path = Path(path)
        metadata = json.loads((path / "metadata.json").read_text())
        if metadata.get("features") != FEATURE_COLUMNS:
            raise RuntimeError(
                "model feature list does not match finguard.features.FEATURE_COLUMNS; "
                "retrain the model or deploy the matching code version"
            )
        return cls(
            estimator=joblib.load(path / "model.joblib"),
            metadata=metadata,
            drift_profile=json.loads((path / "drift_profile.json").read_text()),
            background=pd.read_csv(path / "shap_background.csv"),
        )
