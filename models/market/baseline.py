from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass
class QuantilePrediction:
    q05: float
    q25: float
    q50: float
    q75: float
    q95: float
    probability_positive: float


class EnsembleQuantileBaseline:
    """Baseline probabilistic forecaster using RandomForest tree predictions.

    This is intentionally a baseline, not the final probabilistic model.
    The empirical distribution across trees is used only to create quantile
    outputs. Calibration must be evaluated separately.
    """

    def __init__(self, model: Any):
        self.model = model

    @staticmethod
    def _tree_predictions(model: Any, X) -> np.ndarray:
        if not hasattr(model, "estimators_"):
            raise TypeError("El modelo debe exponer estimators_ (ej. RandomForestRegressor).")
        preds = np.asarray([tree.predict(X) for tree in model.estimators_], dtype=float)
        if preds.ndim == 2 and preds.shape[1] == 1:
            preds = preds[:, 0]
        return preds

    def predict_distribution(self, X) -> QuantilePrediction:
        preds = self._tree_predictions(self.model, X)
        qs = np.percentile(preds, [5, 25, 50, 75, 95])
        return QuantilePrediction(
            q05=float(qs[0]),
            q25=float(qs[1]),
            q50=float(qs[2]),
            q75=float(qs[3]),
            q95=float(qs[4]),
            probability_positive=float(np.mean(preds > 0.0)),
        )
