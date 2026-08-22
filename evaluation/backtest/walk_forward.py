from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from evaluation.metrics.forecast_metrics import (
    directional_accuracy,
    interval_coverage,
    mae,
    mean_baseline_mae,
    pinball_loss,
    zero_baseline_mae,
)
from models.market.baseline_v001 import MarketBaselineV001


@dataclass(frozen=True)
class FoldResult:
    fold: int
    train_rows: int
    test_rows: int
    train_until: str
    test_from: str
    model_mae: float
    zero_mae: float
    mean_mae: float
    directional_accuracy: float
    coverage_90: float
    pinball_q50: float
    pinball_q05: float
    pinball_q95: float


def expanding_walk_forward(
    df: pd.DataFrame,
    feature_columns: list[str],
    min_train_rows: int = 200,
    test_rows: int = 50,
    step_rows: int = 50,
) -> list[FoldResult]:
    data = df.sort_values(["origin_time", "asset_id"]).reset_index(drop=True)
    results: list[FoldResult] = []
    fold = 0
    train_end = min_train_rows

    while train_end < len(data):
        test_end = min(train_end + test_rows, len(data))
        train_df = data.iloc[:train_end]
        test_df = data.iloc[train_end:test_end]
        if len(test_df) == 0:
            break

        horizon_values = test_df["horizon_seconds"].unique()
        if len(horizon_values) != 1:
            raise ValueError("Cada ejecución de walk-forward debe contener un solo horizonte.")

        model = MarketBaselineV001()
        model.fit(
            train_df[feature_columns],
            train_df["return_pct"],
            feature_columns,
            int(horizon_values[0]),
        )
        pred = model.predict_distribution(test_df[feature_columns])

        q05 = np.array([p.q05 for p in pred])
        q50 = np.array([p.q50 for p in pred])
        q95 = np.array([p.q95 for p in pred])
        y = test_df["return_pct"].to_numpy(float)

        results.append(
            FoldResult(
                fold=fold,
                train_rows=len(train_df),
                test_rows=len(test_df),
                train_until=str(train_df["origin_time"].max()),
                test_from=str(test_df["origin_time"].min()),
                model_mae=mae(y, q50),
                zero_mae=zero_baseline_mae(y),
                mean_mae=mean_baseline_mae(train_df["return_pct"], y),
                directional_accuracy=directional_accuracy(y, q50),
                coverage_90=interval_coverage(y, q05, q95),
                pinball_q50=pinball_loss(y, q50, 0.50),
                pinball_q05=pinball_loss(y, q05, 0.05),
                pinball_q95=pinball_loss(y, q95, 0.95),
            )
        )

        fold += 1
        train_end += step_rows

    return results
