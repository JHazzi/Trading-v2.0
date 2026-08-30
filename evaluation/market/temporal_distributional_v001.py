"""Evaluation primitives for shared Q(total return | state, tau).

All assets are averaged inside (origin day, tau), taus are then weighted
equally inside the day, and uncertainty resamples complete origin-day blocks.
This keeps the strong cross-asset and cross-horizon label dependence intact.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd


def pinball(actual: np.ndarray, predicted: np.ndarray, quantile: float) -> np.ndarray:
    y = np.asarray(actual, dtype=float)
    p = np.asarray(predicted, dtype=float)
    q = float(quantile)
    if y.ndim != 1 or y.shape != p.shape or not 0.0 < q < 1.0:
        raise ValueError("invalid pinball inputs")
    error = y - p
    return np.maximum(q * error, (q - 1.0) * error)


def mean_pinball(actual: np.ndarray, predicted: np.ndarray, quantiles: Sequence[float]) -> np.ndarray:
    matrix = np.asarray(predicted, dtype=float)
    qs = tuple(map(float, quantiles))
    if matrix.ndim != 2 or matrix.shape != (len(actual), len(qs)):
        raise ValueError("prediction matrix differs from quantile manifest")
    if np.any(np.diff(matrix, axis=1) < -1e-10):
        raise ValueError("quantile crossing remains after rearrangement")
    return np.mean(np.column_stack([pinball(actual, matrix[:, i], q) for i, q in enumerate(qs)]), axis=1)


def equal_tau_day_table(
    origin_day_index: np.ndarray,
    tau_sessions: np.ndarray,
    baseline_loss: np.ndarray,
    candidate_loss: np.ndarray,
    day_values: Sequence[str],
) -> pd.DataFrame:
    day = np.asarray(origin_day_index, dtype=int)
    tau = np.asarray(tau_sessions, dtype=int)
    base = np.asarray(baseline_loss, dtype=float)
    cand = np.asarray(candidate_loss, dtype=float)
    if not (len(day) == len(tau) == len(base) == len(cand)):
        raise ValueError("comparison arrays differ in length")
    frame = pd.DataFrame({"day_index": day, "tau_sessions": tau, "baseline": base, "candidate": cand})
    by_tau = frame.groupby(["day_index", "tau_sessions"], sort=True, observed=True).agg(
        rows=("candidate", "size"), baseline_loss=("baseline", "mean"), candidate_loss=("candidate", "mean")
    ).reset_index()
    daily = by_tau.groupby("day_index", sort=True, observed=True).agg(
        taus=("tau_sessions", "size"), rows=("rows", "sum"),
        baseline_loss=("baseline_loss", "mean"), candidate_loss=("candidate_loss", "mean")
    ).reset_index()
    daily["origin_trading_day"] = [str(day_values[int(i)]) for i in daily["day_index"]]
    daily["loss_delta_baseline_minus_candidate"] = daily["baseline_loss"] - daily["candidate_loss"]
    return daily[["origin_trading_day", "day_index", "taus", "rows", "baseline_loss", "candidate_loss", "loss_delta_baseline_minus_candidate"]]


def moving_block_bootstrap(values: np.ndarray, block_length: int, reps: int, seed: int) -> dict[str, Any]:
    data = np.asarray(values, dtype=float)
    if data.ndim != 1 or len(data) < 2 or not np.isfinite(data).all():
        raise ValueError("invalid daily bootstrap values")
    if not 1 <= int(block_length) <= len(data) or int(reps) < 100:
        raise ValueError("invalid moving-block contract")
    rng = np.random.default_rng(int(seed))
    starts = np.arange(len(data) - int(block_length) + 1)
    draws = np.empty(int(reps), dtype=float)
    blocks_needed = int(np.ceil(len(data) / int(block_length)))
    for rep in range(int(reps)):
        sampled_starts = rng.choice(starts, size=blocks_needed, replace=True)
        sample = np.concatenate([data[s:s + int(block_length)] for s in sampled_starts])[:len(data)]
        draws[rep] = float(np.mean(sample))
    return {
        "point_delta_pct": float(np.mean(data)),
        "ci95": [float(np.quantile(draws, 0.025)), float(np.quantile(draws, 0.975))],
        "unique_origin_days": int(len(data)),
        "block_length_origin_days": int(block_length),
        "bootstrap_method": "noncircular_moving_block_complete_cross_tau_day_vectors",
        "bootstrap_repetitions": int(reps),
        "bootstrap_seed": int(seed),
        "positive_delta_means_candidate_lower_loss": True,
    }


def calibration_counts(actual: np.ndarray, prediction: np.ndarray, quantiles: Sequence[float]) -> dict[str, Any]:
    y = np.asarray(actual, dtype=float)
    p = np.asarray(prediction, dtype=float)
    result: dict[str, Any] = {"rows": int(len(y)), "per_quantile": {}}
    for index, q in enumerate(map(float, quantiles)):
        hits = int(np.count_nonzero(y <= p[:, index]))
        result["per_quantile"][str(q)] = {
            "quantile": q,
            "cdf_hits": hits,
            "rows": int(len(y)),
            "empirical_cdf": float(hits / len(y)),
            "calibration_error": float(hits / len(y) - q),
        }
    return result


def equal_tau_day_calibration(
    origin_day_index: np.ndarray,
    tau_sessions: np.ndarray,
    actual: np.ndarray,
    prediction: np.ndarray,
    quantiles: Sequence[float],
    day_values: Sequence[str],
) -> dict[str, list[dict[str, Any]]]:
    """Return day-level empirical CDFs after equal weighting of taus."""
    result: dict[str, list[dict[str, Any]]] = {}
    for column, q in enumerate(map(float, quantiles)):
        frame = pd.DataFrame({
            "day_index": np.asarray(origin_day_index, dtype=int),
            "tau_sessions": np.asarray(tau_sessions, dtype=int),
            "hit": (np.asarray(actual, dtype=float) <= np.asarray(prediction, dtype=float)[:, column]).astype(float),
        })
        by_tau = frame.groupby(["day_index", "tau_sessions"], sort=True, observed=True)["hit"].mean().reset_index()
        daily = by_tau.groupby("day_index", sort=True, observed=True)["hit"].mean().reset_index()
        result[str(q)] = [
            {"origin_trading_day": str(day_values[int(row.day_index)]), "empirical_cdf": float(row.hit)}
            for row in daily.itertuples(index=False)
        ]
    return result


def combine_calibration(parts: Sequence[Mapping[str, Any]], quantiles: Sequence[float]) -> dict[str, Any]:
    per: dict[str, Any] = {}
    for q in map(float, quantiles):
        key = str(q)
        hits = sum(int(part["per_quantile"][key]["cdf_hits"]) for part in parts)
        rows = sum(int(part["per_quantile"][key]["rows"]) for part in parts)
        per[key] = {
            "quantile": q, "cdf_hits": hits, "rows": rows,
            "empirical_cdf": float(hits / rows), "calibration_error": float(hits / rows - q),
        }
    return {
        "rows": sum(int(part["rows"]) for part in parts),
        "per_quantile": per,
        "mean_absolute_quantile_calibration_error": float(np.mean([abs(x["calibration_error"]) for x in per.values()])),
    }


def development_gate(summary: Mapping[str, Any], rules: Mapping[str, Any]) -> dict[str, Any]:
    primary_ci = list(map(float, summary["candidate_vs_reference"]["bootstrap"]["252"]["ci95"]))
    checks = {
        "primary_interval_lower_bound_positive": primary_ci[0] > 0.0,
        "minimum_positive_anchors_met": int(summary["positive_anchors"]) >= int(rules["minimum_positive_anchors"]),
        "minimum_positive_folds_met": int(summary["positive_folds"]) >= int(rules["minimum_positive_folds"]),
        "minimum_improved_quantiles_met": int(summary["improved_quantiles"]) >= int(rules["minimum_improved_quantiles"]),
        "candidate_calibration_not_worse": float(summary["candidate_calibration_mae"]) <= float(summary["reference_calibration_mae"]),
        "candidate_beats_mean_placebo_interval": float(summary["candidate_vs_mean_placebo"]["bootstrap"]["252"]["ci95"][0]) > 0.0,
        "candidate_beats_every_placebo_seed_point": all(float(x) > 0.0 for x in summary["candidate_vs_each_placebo_point"].values()),
    }
    required = list(checks.values())
    point = float(summary["candidate_vs_reference"]["point_delta_pct"])
    if all(required):
        status = "PASS_DEVELOPMENTAL_REQUIRES_FRESH_HOLDOUT"
    elif primary_ci[1] < 0.0 or point <= 0.0:
        status = "FAIL_CLOSE_TEMPORAL_DISTRIBUTIONAL_BRANCH"
    else:
        status = "INCONCLUSIVE_OR_AUXILIARY_GATE_FAIL_NO_HOLDOUT_OPEN"
    return {"status": status, "checks": checks, "primary_ci95": primary_ci, "primary_point_delta_pct": point}


def holdout_gate(summary: Mapping[str, Any], rules: Mapping[str, Any]) -> dict[str, Any]:
    ci = list(map(float, summary["candidate_vs_reference"]["bootstrap"]["252"]["ci95"]))
    checks = {
        "primary_interval_lower_bound_positive": ci[0] > 0.0,
        "minimum_positive_holdout_horizons_met": int(summary["positive_holdout_horizons"]) >= int(rules["minimum_positive_holdout_horizons"]),
        "minimum_positive_folds_met": int(summary["positive_folds"]) >= int(rules["minimum_positive_folds"]),
        "minimum_improved_quantiles_met": int(summary["improved_quantiles"]) >= int(rules["minimum_improved_quantiles"]),
        "candidate_calibration_not_worse": float(summary["candidate_calibration_mae"]) <= float(summary["reference_calibration_mae"]),
    }
    return {
        "status": "PASS_TEMPORAL_DISTRIBUTIONAL_V001_HOLDOUT" if all(checks.values()) else "FAIL_CLOSE_TEMPORAL_DISTRIBUTIONAL_BRANCH",
        "checks": checks,
        "primary_ci95": ci,
        "primary_point_delta_pct": float(summary["candidate_vs_reference"]["point_delta_pct"]),
        "no_contingency_or_refit_allowed": True,
    }
