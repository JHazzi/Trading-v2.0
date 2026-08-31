"""Exact model mathematics for Temporal Distributional V002.

The base and residual are fitted in log-total-wealth space.  The q-specific
residual is added to the matching base quantile, shrunk by a frozen smooth
function of tau, monotonically rearranged, then back-transformed to percent.
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


def horizon_shrinkage(
    tau_sessions: np.ndarray | Sequence[int] | int,
    half_life_sessions: float = 63.0,
) -> np.ndarray:
    tau = np.asarray(tau_sessions, dtype=float).reshape(-1)
    tau_coordinates(tau)
    half_life = float(half_life_sessions)
    if not np.isfinite(half_life) or half_life <= 0.0:
        raise ValueError("half-life must be positive and finite")
    return np.clip(np.power(2.0, -(tau - 1.0) / half_life), 0.0, 1.0)


def residual_targets(actual_log_wealth: np.ndarray, base_oof_log_quantiles: np.ndarray) -> np.ndarray:
    actual = np.asarray(actual_log_wealth, dtype=float).reshape(-1)
    base = np.asarray(base_oof_log_quantiles, dtype=float)
    if base.ndim != 2 or len(actual) != len(base) or not np.isfinite(base).all():
        raise ValueError("invalid OOF base matrix")
    return actual[:, None] - base


def combine_log_quantiles(
    base_log_quantiles: np.ndarray,
    residual_log_quantiles: np.ndarray,
    tau_sessions: np.ndarray | Sequence[int],
    half_life_sessions: float = 63.0,
) -> np.ndarray:
    base = np.asarray(base_log_quantiles, dtype=float)
    residual = np.asarray(residual_log_quantiles, dtype=float)
    if base.ndim != 2 or base.shape != residual.shape:
        raise ValueError("base and residual prediction matrices must match")
    alpha = horizon_shrinkage(tau_sessions, half_life_sessions)
    if len(alpha) != len(base):
        raise ValueError("one tau is required per prediction row")
    return monotone_rearrange(base + alpha[:, None] * residual)


def _design(own_state: np.ndarray, tau_sessions: np.ndarray | Sequence[int]) -> np.ndarray:
    own = np.asarray(own_state, dtype="float32")
    if own.ndim == 1:
        own = own.reshape(1, -1)
    coords = tau_coordinates(tau_sessions)
    if len(coords) != len(own):
        raise ValueError("one tau is required per state row")
    return np.column_stack((own, coords)).astype("float32")


def predict_bundle(
    bundle: Mapping[str, Any],
    own_state: np.ndarray,
    tau_sessions: np.ndarray | Sequence[int],
) -> np.ndarray:
    own = np.asarray(own_state, dtype="float32")
    features = list(bundle["own_features"])
    if own.ndim != 2 or own.shape[1] != len(features):
        raise ValueError("own-state feature width differs from frozen manifest")
    design = _design(own, tau_sessions)
    quantiles = list(map(float, bundle["quantiles"]))
    scale_index = features.index(bundle["scale_feature"])
    base_design = np.column_stack((own[:, scale_index], design[:, -3:])).astype("float32")
    base_log = np.column_stack([bundle["base_models"][str(q)].predict(base_design) for q in quantiles])
    residual_log = np.column_stack([bundle["residual_models"][str(q)].predict(design) for q in quantiles])
    combined = combine_log_quantiles(
        base_log, residual_log, tau_sessions,
        float(bundle["residual_regularization"]["half_life_sessions"]),
    )
    return fit_to_target(combined).astype("float32")


def load_frozen_bundle(path: Path | str) -> dict[str, Any]:
    bundle = joblib.load(Path(path))
    required = {
        "version", "quantiles", "own_features", "scale_feature",
        "base_models", "residual_models", "residual_regularization",
    }
    if not required.issubset(bundle):
        raise ValueError("invalid temporal residual model bundle")
    if bundle["version"] != "market_temporal_distributional_model_bundle_v002":
        raise ValueError("unsupported temporal residual model bundle")
    return bundle
