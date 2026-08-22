from __future__ import annotations

import numpy as np


def mae(y_true, y_pred) -> float:
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    return float(np.mean(np.abs(y_true - y_pred)))


def rmse(y_true, y_pred) -> float:
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    return float(np.sqrt(np.mean((y_true - y_pred) ** 2)))


def directional_accuracy(y_true, y_pred, neutral_band: float = 0.0) -> float:
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    mask = np.abs(y_true) > neutral_band
    if not np.any(mask):
        return float("nan")
    return float(np.mean(np.sign(y_true[mask]) == np.sign(y_pred[mask])))


def pinball_loss(y_true, q_pred, quantile: float) -> float:
    y_true = np.asarray(y_true, dtype=float)
    q_pred = np.asarray(q_pred, dtype=float)
    diff = y_true - q_pred
    return float(np.mean(np.maximum(quantile * diff, (quantile - 1.0) * diff)))


def interval_coverage(y_true, lower, upper) -> float:
    y_true = np.asarray(y_true, dtype=float)
    lower = np.asarray(lower, dtype=float)
    upper = np.asarray(upper, dtype=float)
    return float(np.mean((y_true >= lower) & (y_true <= upper)))


def zero_baseline_mae(y_true) -> float:
    y_true = np.asarray(y_true, dtype=float)
    return float(np.mean(np.abs(y_true)))


def mean_baseline_mae(y_train, y_test) -> float:
    y_train = np.asarray(y_train, dtype=float)
    y_test = np.asarray(y_test, dtype=float)
    pred = float(np.mean(y_train))
    return float(np.mean(np.abs(y_test - pred)))
