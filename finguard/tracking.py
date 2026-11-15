"""Shared MLflow setup for training and drift runs."""

from __future__ import annotations

import mlflow

from finguard.config import MLFLOW_ARTIFACT_ROOT, MLFLOW_TRACKING_URI


def setup_mlflow(experiment: str) -> None:
    """Point MLflow at the configured store and select (or create) ``experiment``.

    With a local store (sqlite/file), artifacts go to ``mlartifacts/``; with a
    tracking server, the server decides (artifact proxying).
    """
    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    if mlflow.get_experiment_by_name(experiment) is None:
        local = not MLFLOW_TRACKING_URI.startswith("http")
        mlflow.create_experiment(
            experiment,
            artifact_location=MLFLOW_ARTIFACT_ROOT.as_uri() if local else None,
            tags={"project": "finguard"},
        )
    mlflow.set_experiment(experiment)
