"""Online drift monitor: rolling window of live traffic vs the training reference."""

from __future__ import annotations

import threading
import time
from collections import deque

import numpy as np
import pandas as pd

from finguard.api import metrics
from finguard.drift import compute_drift


class DriftMonitor:
    def __init__(self, profile: dict, window_size: int = 2000, min_samples: int = 200, drift_share: float = 0.3):
        self.profile = profile
        self.window_size = window_size
        self.min_samples = min_samples
        self.drift_share = drift_share
        self._rows: deque[dict] = deque(maxlen=window_size)
        self._scores: deque[float] = deque(maxlen=window_size)
        self._lock = threading.Lock()
        self.last_report: dict = {"status": "warming_up", "n_samples": 0, "min_samples": min_samples}

    def observe(self, frame: pd.DataFrame, scores: np.ndarray) -> None:
        records = frame.to_dict("records")
        with self._lock:
            self._rows.extend(records)
            self._scores.extend(float(s) for s in scores)
        metrics.DRIFT_WINDOW.set(len(self._rows))

    def reset(self) -> None:
        with self._lock:
            self._rows.clear()
            self._scores.clear()
        metrics.DRIFT_WINDOW.set(0)
        metrics.DRIFT_DETECTED.set(0)
        self.last_report = {"status": "warming_up", "n_samples": 0, "min_samples": self.min_samples}

    def evaluate(self) -> dict:
        with self._lock:
            rows = list(self._rows)
            scores = np.fromiter(self._scores, dtype=float, count=len(self._scores))
        metrics.DRIFT_WINDOW.set(len(rows))
        metrics.DRIFT_LAST_RUN.set(time.time())
        if len(rows) < self.min_samples:
            self.last_report = {"status": "warming_up", "n_samples": len(rows), "min_samples": self.min_samples}
            return self.last_report

        report = compute_drift(self.profile, pd.DataFrame(rows), scores, drift_share=self.drift_share)
        report["status"] = "drift" if report["drift_detected"] else "ok"
        report["checked_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        report["window_size"] = self.window_size

        for name, feat in report["features"].items():
            metrics.FEATURE_PSI.labels(feature=name).set(feat["psi"])
        if "prediction" in report:
            metrics.PREDICTION_PSI.set(report["prediction"]["psi"])
        metrics.DRIFT_SHARE.set(report["drift_share"])
        metrics.DRIFT_DETECTED.set(int(report["drift_detected"]))
        self.last_report = report
        return report
