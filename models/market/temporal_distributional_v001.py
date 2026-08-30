"""Shared horizon-conditioned quantile model utilities.

The model has no horizon-specific heads.  Tau is an input coordinate, so one
frozen bundle can answer every integer horizon in [1, 252].  Sparse anchors
are a compute/evaluation design, not the mathematical output domain.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

import joblib
import numpy as np


def tau_coordinates(tau_sessions: np.ndarray | Sequence[int] | int) -> np.ndarray:
    tau = np.asarray(tau_sessions, dtype=float).reshape(-1)
    if not np.isfinite(tau).all() or np.any(tau != np.floor(tau)):
        raise ValueError("tau must contain finite integer session counts")
    if np.any((tau < 1) | (tau > 252)):
        raise ValueError("tau is outside the frozen [1,252] session domain")
    return np.column_stack((tau, tau / 252.0, np.log1p(tau) / np.log(253.0))).astype("float32")


def target_to_fit(total_return_pct: np.ndarray | Sequence[float]) -> np.ndarray:
    value = np.asarray(total_return_pct, dtype=float)
    if not np.isfinite(value).all() or np.any(value <= -100.0):
        raise ValueError("total shareholder return must be finite and strictly above -100%")
    return np.log1p(value / 100.0)


def fit_to_target(predicted_log_total_wealth: np.ndarray | Sequence[float]) -> np.ndarray:
    value = np.asarray(predicted_log_total_wealth, dtype=float)
    if not np.isfinite(value).all():
        raise ValueError("nonfinite log-wealth prediction")
    return 100.0 * np.expm1(value)


def monotone_rearrange(matrix: np.ndarray) -> np.ndarray:
    value = np.asarray(matrix, dtype=float)
    if value.ndim != 2 or not np.isfinite(value).all():
        raise ValueError("prediction matrix must be finite and two-dimensional")
    return np.sort(value, axis=1)


def predict_bundle(
    bundle: Mapping[str, Any],
    own_state: np.ndarray,
    tau_sessions: np.ndarray | Sequence[int] | int,
) -> np.ndarray:
    own = np.asarray(own_state, dtype="float32")
    if own.ndim == 1:
        own = own.reshape(1, -1)
    tau = np.asarray(tau_sessions)
    if tau.ndim == 0:
        tau = np.repeat(tau.reshape(1), len(own))
    coords = tau_coordinates(tau)
    if len(coords) != len(own):
        raise ValueError("one tau is required per state row")
    expected = list(bundle["own_features"])
    if own.shape[1] != len(expected):
        raise ValueError("own-state feature width differs from frozen manifest")
    design = np.column_stack((own, coords)).astype("float32")
    raw = np.column_stack([
        bundle["models"][str(q)].predict(design) for q in bundle["quantiles"]
    ])
    return monotone_rearrange(fit_to_target(raw))


def load_frozen_bundle(path: Path | str) -> dict[str, Any]:
    bundle = joblib.load(Path(path))
    required = {"version", "quantiles", "own_features", "models", "target_representation"}
    if not required.issubset(bundle):
        raise ValueError("invalid temporal distributional model bundle")
    if bundle["version"] != "market_temporal_distributional_model_bundle_v001":
        raise ValueError("unsupported temporal model bundle")
    return bundle
