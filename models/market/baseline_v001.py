"""Simple probabilistic market baseline.

This is deliberately a benchmark, not the final trajectory model.
The tree ensemble is used to produce empirical quantiles and a raw positive-return
frequency. Those values are NOT treated as calibrated probabilities yet.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sklearn.ensemble import RandomForestRegressor


@dataclass
class BaselinePrediction:
    q05: float
    q25: float
    q50: float
    q75: float
    q95: float
    probability_positive_raw: float


class MarketBaselineV001:
    version = 'market_baseline_v0.1.0'

    def __init__(
        self,
        n_estimators: int = 300,
        min_samples_leaf: int = 5,
        random_state: int = 42,
    ) -> None:
        self.model = RandomForestRegressor(
            n_estimators=n_estimators,
            min_samples_leaf=min_samples_leaf,
            max_features='sqrt',
            random_state=random_state,
            n_jobs=-1,
        )
        self.feature_columns: list[str] = []
        self.horizon_seconds: int | None = None

    def fit(self, X, y, feature_columns: list[str], horizon_seconds: int) -> 'MarketBaselineV001':
        self.model.fit(X, y)
        self.feature_columns = list(feature_columns)
        self.horizon_seconds = int(horizon_seconds)
        return self

    def _tree_predictions(self, X) -> np.ndarray:
        preds = np.column_stack([tree.predict(X) for tree in self.model.estimators_])
        return preds

    def predict_distribution(self, X) -> list[BaselinePrediction]:
        preds = self._tree_predictions(X)
        quantiles = np.quantile(preds, [0.05, 0.25, 0.50, 0.75, 0.95], axis=1)
        p_positive = np.mean(preds > 0.0, axis=1)
        return [
            BaselinePrediction(
                q05=float(quantiles[0, i]),
                q25=float(quantiles[1, i]),
                q50=float(quantiles[2, i]),
                q75=float(quantiles[3, i]),
                q95=float(quantiles[4, i]),
                probability_positive_raw=float(p_positive[i]),
            )
            for i in range(preds.shape[0])
        ]
