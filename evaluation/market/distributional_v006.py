from __future__ import annotations

from typing import Mapping

import numpy as np
import pandas as pd


def quantile_name(q: float) -> str:
    pct = int(round(100.0 * float(q)))
    return f"q{pct:02d}"


def pinball_rows(
    actual: np.ndarray,
    prediction: np.ndarray,
    quantile: float,
) -> np.ndarray:
    y = np.asarray(actual, dtype=float)
    p = np.asarray(prediction, dtype=float)
    q = float(quantile)
    if y.shape != p.shape or y.ndim != 1:
        raise ValueError("actual and prediction must be equal one-dimensional arrays")
    if not 0.0 < q < 1.0:
        raise ValueError("quantile must be strictly between zero and one")
    error = y - p
    return np.maximum(q * error, (q - 1.0) * error)


def validate_distribution_predictions(
    predictions: Mapping[float, np.ndarray],
    probability_positive: np.ndarray,
) -> tuple[list[float], np.ndarray]:
    quantiles = sorted(float(q) for q in predictions)
    if not quantiles:
        raise ValueError("at least one quantile is required")
    matrix = np.column_stack([
        np.asarray(predictions[q], dtype=float) for q in quantiles
    ])
    probability = np.asarray(probability_positive, dtype=float)
    if matrix.ndim != 2 or len(matrix) != len(probability):
        raise ValueError("prediction lengths differ")
    if not np.isfinite(matrix).all() or not np.isfinite(probability).all():
        raise ValueError("nonfinite distribution prediction")
    if np.any(np.diff(matrix, axis=1) < -1e-12):
        raise ValueError("quantile crossing")
    if np.any((probability < 0.0) | (probability > 1.0)):
        raise ValueError("probability outside [0,1]")
    return quantiles, matrix


def distribution_metrics(
    actual: np.ndarray,
    predictions: Mapping[float, np.ndarray],
    probability_positive: np.ndarray,
) -> dict[str, object]:
    y = np.asarray(actual, dtype=float)
    quantiles, matrix = validate_distribution_predictions(
        predictions, probability_positive
    )
    if y.ndim != 1 or len(y) != len(matrix) or not np.isfinite(y).all():
        raise ValueError("invalid actual outcomes")

    per_quantile: dict[str, dict[str, float]] = {}
    row_losses = []
    for index, q in enumerate(quantiles):
        loss = pinball_rows(y, matrix[:, index], q)
        row_losses.append(loss)
        per_quantile[quantile_name(q)] = {
            "quantile": q,
            "pinball_loss_pct": float(np.mean(loss)),
            "empirical_cdf_at_prediction": float(
                np.mean(y <= matrix[:, index])
            ),
            "calibration_error": float(np.mean(y <= matrix[:, index]) - q),
        }

    prob = np.asarray(probability_positive, dtype=float)
    positive = (y > 0.0).astype(float)
    result: dict[str, object] = {
        "rows": int(len(y)),
        "mean_pinball_loss_pct": float(
            np.mean(np.column_stack(row_losses))
        ),
        "positive_return_brier": float(np.mean((prob - positive) ** 2)),
        "positive_return_base_rate": float(np.mean(positive)),
        "predicted_positive_probability_mean": float(np.mean(prob)),
        "per_quantile": per_quantile,
    }

    lookup = {q: i for i, q in enumerate(quantiles)}
    if 0.5 in lookup:
        median = matrix[:, lookup[0.5]]
        result["median_mae_pct"] = float(np.mean(np.abs(y - median)))

    for lower, upper, label in (
        (0.25, 0.75, "central_50"),
        (0.05, 0.95, "central_90"),
    ):
        if lower in lookup and upper in lookup:
            lo = matrix[:, lookup[lower]]
            hi = matrix[:, lookup[upper]]
            result[label] = {
                "coverage": float(np.mean((y >= lo) & (y <= hi))),
                "mean_width_pct": float(np.mean(hi - lo)),
                "coverage_error": float(
                    np.mean((y >= lo) & (y <= hi)) - (upper - lower)
                ),
            }
    return result


def mean_pinball_rows(
    actual: np.ndarray,
    predictions: Mapping[float, np.ndarray],
) -> np.ndarray:
    y = np.asarray(actual, dtype=float)
    losses = [
        pinball_rows(y, np.asarray(predictions[q], dtype=float), float(q))
        for q in sorted(predictions)
    ]
    return np.mean(np.column_stack(losses), axis=1)


def daily_loss_comparison(
    origin_days: np.ndarray | pd.Series,
    baseline_loss: np.ndarray,
    candidate_loss: np.ndarray,
) -> pd.DataFrame:
    days = pd.Series(origin_days, dtype=str)
    base = np.asarray(baseline_loss, dtype=float)
    candidate = np.asarray(candidate_loss, dtype=float)
    if len(days) != len(base) or len(base) != len(candidate):
        raise ValueError("daily loss inputs differ in length")
    frame = pd.DataFrame({
        "origin_trading_day": days,
        "baseline_loss": base,
        "candidate_loss": candidate,
    })
    frame["loss_delta_baseline_minus_candidate"] = (
        frame["baseline_loss"] - frame["candidate_loss"]
    )
    daily = frame.groupby("origin_trading_day", sort=True).agg(
        rows=("loss_delta_baseline_minus_candidate", "size"),
        baseline_loss=("baseline_loss", "mean"),
        candidate_loss=("candidate_loss", "mean"),
        loss_delta_baseline_minus_candidate=(
            "loss_delta_baseline_minus_candidate", "mean"
        ),
    ).reset_index()
    return daily


def moving_block_bootstrap_daily_loss(
    daily: pd.DataFrame,
    *,
    block_length: int,
    reps: int,
    seed: int,
) -> dict[str, object]:
    required = {
        "origin_trading_day",
        "loss_delta_baseline_minus_candidate",
    }
    if not required.issubset(daily.columns):
        raise ValueError("daily comparison columns missing")
    values = daily.sort_values("origin_trading_day")[
        "loss_delta_baseline_minus_candidate"
    ].to_numpy(float)
    if len(values) < 2 or block_length < 1 or block_length > len(values):
        raise ValueError("invalid moving-block request")
    if reps < 100:
        raise ValueError("too few bootstrap repetitions")

    rng = np.random.default_rng(int(seed))
    starts = np.arange(0, len(values) - int(block_length) + 1)
    boot = np.empty(int(reps), dtype=float)
    for rep in range(int(reps)):
        sampled: list[float] = []
        while len(sampled) < len(values):
            start = int(rng.choice(starts))
            sampled.extend(
                values[start:start + int(block_length)].tolist()
            )
        boot[rep] = float(np.mean(sampled[:len(values)]))

    return {
        "point_delta_pct": float(np.mean(values)),
        "ci95": [
            float(np.quantile(boot, 0.025)),
            float(np.quantile(boot, 0.975)),
        ],
        "unique_origin_days": int(len(values)),
        "block_length_origin_days": int(block_length),
        "bootstrap_method": "noncircular_moving_block",
        "bootstrap_reps": int(reps),
        "bootstrap_seed": int(seed),
        "positive_delta_means_candidate_lower_loss": True,
    }
