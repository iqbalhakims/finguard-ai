"""FinGuard AI — miniature banking fraud detection platform."""

import os

# MLflow 3.x prints an agent hint on import; keep CLI output clean.
os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")

__version__ = "0.1.0"
