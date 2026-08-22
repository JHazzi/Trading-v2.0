from __future__ import annotations

import numpy as np
import pandas as pd


def zero_prediction(train: pd.DataFrame, test: pd.DataFrame) -> np.ndarray:
    return np.zeros(len(test), dtype=float)


def asset_mean_prediction(
    train: pd.DataFrame,
    test: pd.DataFrame,
    target: str = "return_pct",
) -> np.ndarray:
    """Predict each asset with its mean return in TRAIN only.

    Unseen test assets fall back to the global train mean.
    """
    global_mean = float(train[target].mean())
    means = train.groupby("asset_id")[target].mean()

    pred = test["asset_id"].map(means).fillna(global_mean).to_numpy(dtype=float)
    return pred


def global_mean_prediction(
    train: pd.DataFrame,
    test: pd.DataFrame,
    target: str = "return_pct",
) -> np.ndarray:
    return np.full(len(test), float(train[target].mean()), dtype=float)
