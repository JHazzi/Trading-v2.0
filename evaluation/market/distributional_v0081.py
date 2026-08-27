from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from evaluation.market.daily_v003_benchmark import build_purged_day_folds, fold_summary
from evaluation.market.distributional_v006 import (
    daily_loss_comparison,
    distribution_metrics,
    mean_pinball_rows,
    moving_block_bootstrap_daily_loss,
    pinball_rows,
    quantile_name,
)
from models.market.distributional_v008_conditional_quantiles import load_horizon
from models.market.distributional_v0081_endogenous_closure import (
    fit_raw_model_bundle,
    fit_raw_vol63_bundle,
    make_capacity_placebo_train,
)


def _json_sha(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


def _store_bundle(frame: pd.DataFrame, prefix: str, bundle: Mapping[str, Any]) -> None:
    for q, values in bundle["quantiles"].items():
        frame[f"{prefix}_{quantile_name(float(q))}"] = np.asarray(values, dtype="float32")
    frame[f"{prefix}_prob_positive"] = np.asarray(bundle["probability_positive"], dtype="float32")


def _bundle_from_columns(
    frame: pd.DataFrame,
    prefix: str,
    quantiles: tuple[float, ...],
) -> dict[str, Any]:
    return {
        "quantiles": {
            q: frame[f"{prefix}_{quantile_name(q)}"].to_numpy(float)
            for q in quantiles
        },
        "probability_positive": frame[f"{prefix}_prob_positive"].to_numpy(float),
    }


def _loss_comparison(
    origin_days: pd.Series,
    baseline_loss: np.ndarray,
    candidate_loss: np.ndarray,
    cfg: Mapping[str, Any],
) -> tuple[dict[str, Any], pd.DataFrame]:
    daily = daily_loss_comparison(origin_days, baseline_loss, candidate_loss)
    bootstrap = {}
    for block in cfg["moving_block_lengths_origin_days"]:
        bootstrap[str(block)] = moving_block_bootstrap_daily_loss(
            daily,
            block_length=int(block),
            reps=int(cfg["bootstrap_reps"]),
            seed=int(cfg["bootstrap_seed"]),
        )
    return {
        "origin_day_equal_weight_delta_pct": float(
            daily["loss_delta_baseline_minus_candidate"].mean()
        ),
        "row_weighted_delta_pct": float(np.mean(baseline_loss - candidate_loss)),
        "positive_delta_means_candidate_lower_loss": True,
        "moving_block_bootstrap": bootstrap,
    }, daily


def _comparison(
    frame: pd.DataFrame,
    actual: np.ndarray,
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
    cfg: Mapping[str, Any],
) -> tuple[dict[str, Any], pd.DataFrame]:
    return _loss_comparison(
        frame["origin_trading_day"],
        mean_pinball_rows(actual, baseline["quantiles"]),
        mean_pinball_rows(actual, candidate["quantiles"]),
        cfg,
    )


def _per_quantile_comparison(
    frame: pd.DataFrame,
    actual: np.ndarray,
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> dict[str, Any]:
    out = {}
    for q in sorted(float(x) for x in baseline["quantiles"]):
        base_loss = pinball_rows(actual, baseline["quantiles"][q], q)
        candidate_loss = pinball_rows(actual, candidate["quantiles"][q], q)
        daily = daily_loss_comparison(frame["origin_trading_day"], base_loss, candidate_loss)
        out[quantile_name(q)] = {
            "quantile": q,
            "origin_day_equal_weight_delta_pct": float(
                daily["loss_delta_baseline_minus_candidate"].mean()
            ),
            "row_weighted_delta_pct": float(np.mean(base_loss - candidate_loss)),
            "positive_delta_means_candidate_lower_loss": True,
        }
    return out


def mean_absolute_quantile_calibration_error(metrics: Mapping[str, Any]) -> float:
    return float(
        np.mean([
            abs(float(value["calibration_error"]))
            for value in metrics["per_quantile"].values()
        ])
    )


def _group_delta_table(
    frame: pd.DataFrame,
    row_delta: np.ndarray,
    group_column: str,
) -> pd.DataFrame:
    values = frame[group_column].fillna("UNKNOWN").astype(str)
    work = pd.DataFrame({
        group_column: values,
        "origin_trading_day": frame["origin_trading_day"].astype(str),
        "loss_delta_baseline_minus_candidate": np.asarray(row_delta, dtype=float),
    })
    return (
        work.groupby(group_column, sort=True)
        .agg(
            rows=("loss_delta_baseline_minus_candidate", "size"),
            origin_days=("origin_trading_day", "nunique"),
            mean_delta_pct=("loss_delta_baseline_minus_candidate", "mean"),
        )
        .reset_index()
        .sort_values(["mean_delta_pct", group_column], ascending=[False, True], kind="mergesort")
        .reset_index(drop=True)
    )


def _group_summary(table: pd.DataFrame) -> dict[str, Any]:
    positive = table["mean_delta_pct"].to_numpy(float) > 0.0
    return {
        "groups": int(len(table)),
        "positive_groups": int(positive.sum()),
        "positive_group_fraction": float(np.mean(positive)) if len(table) else 0.0,
        "best_group_delta_pct": float(table["mean_delta_pct"].max()) if len(table) else None,
        "worst_group_delta_pct": float(table["mean_delta_pct"].min()) if len(table) else None,
    }


def evaluate_horizon_gate(
    horizon: int,
    primary: Mapping[str, Any],
    candidate_metrics: Mapping[str, Any],
    reference_metrics: Mapping[str, Any],
    per_quantile: Mapping[str, Any],
    fold_table: pd.DataFrame,
    placebo_mean: Mapping[str, Any] | None,
    placebo_seed_comparisons: Mapping[str, Any],
    cfg: Mapping[str, Any],
) -> dict[str, Any]:
    primary_horizon = int(cfg["primary_horizon_sessions"])
    if int(horizon) != primary_horizon:
        return {
            "status": "DIAGNOSTIC_ONLY",
            "reason": "only H1 was prospectively designated as the V008.1 closure hypothesis",
            "may_rescue_primary_horizon": False,
        }

    rules = cfg["primary_gate"]
    block = str(int(rules["bootstrap_block_length_origin_days"]))
    primary_ci = [float(x) for x in primary["moving_block_bootstrap"][block]["ci95"]]
    calibration_candidate = mean_absolute_quantile_calibration_error(candidate_metrics)
    calibration_reference = mean_absolute_quantile_calibration_error(reference_metrics)
    calibration_not_worse = calibration_candidate <= calibration_reference
    improved_quantiles = sum(
        float(value["origin_day_equal_weight_delta_pct"]) > 0.0
        for value in per_quantile.values()
    )
    positive_folds = int((fold_table["mean_delta_pct"].to_numpy(float) > 0.0).sum())

    placebo_mean_ci = None
    placebo_mean_pass = False
    if placebo_mean is not None:
        placebo_mean_ci = [
            float(x) for x in placebo_mean["moving_block_bootstrap"][block]["ci95"]
        ]
        placebo_mean_pass = placebo_mean_ci[0] > 0.0
    placebo_seed_points = {
        str(seed): float(result["origin_day_equal_weight_delta_pct"])
        for seed, result in placebo_seed_comparisons.items()
    }
    beats_every_placebo_point = bool(placebo_seed_points) and all(
        value > 0.0 for value in placebo_seed_points.values()
    )

    checks = {
        "primary_score_ci_positive": primary_ci[0] > 0.0,
        "candidate_calibration_not_worse": calibration_not_worse,
        "minimum_positive_folds_met": positive_folds >= int(rules["minimum_positive_folds"]),
        "minimum_improved_quantiles_met": improved_quantiles >= int(rules["minimum_improved_quantiles"]),
        "candidate_beats_mean_placebo_ci": placebo_mean_pass,
        "candidate_beats_every_placebo_point": beats_every_placebo_point,
    }
    required = [
        checks["primary_score_ci_positive"],
        checks["minimum_positive_folds_met"],
        checks["minimum_improved_quantiles_met"],
    ]
    if bool(rules["require_candidate_calibration_not_worse"]):
        required.append(checks["candidate_calibration_not_worse"])
    if bool(rules["require_candidate_beats_mean_placebo_ci"]):
        required.append(checks["candidate_beats_mean_placebo_ci"])
    if bool(rules["require_candidate_beats_every_placebo_point"]):
        required.append(checks["candidate_beats_every_placebo_point"])

    point = float(primary["origin_day_equal_weight_delta_pct"])
    if all(required):
        status = "PASS_DEVELOPMENTAL_REQUIRES_FRESH_HOLDOUT"
    elif primary_ci[1] < 0.0:
        status = "FAIL_SIGNIFICANT_CLOSE_ENDOGENOUS_BRANCH"
    elif point <= 0.0:
        status = "FAIL_CLOSE_ENDOGENOUS_BRANCH"
    else:
        status = "INCONCLUSIVE_OR_AUXILIARY_GATE_FAIL_NO_PROMOTION"

    return {
        "status": status,
        "primary_point_delta_pct": point,
        "primary_block_ci95": primary_ci,
        "bootstrap_block_length_origin_days": int(block),
        "candidate_mean_abs_quantile_calibration_error": calibration_candidate,
        "reference_mean_abs_quantile_calibration_error": calibration_reference,
        "improved_quantiles": int(improved_quantiles),
        "positive_folds": positive_folds,
        "placebo_mean_block_ci95": placebo_mean_ci,
        "placebo_seed_point_deltas_pct": placebo_seed_points,
        "checks": checks,
        "fresh_untouched_holdout_required_for_promotion": True,
    }


def run_horizon(
    core_db: Path,
    horizon: int,
    cfg: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> tuple[dict[str, Any], pd.DataFrame, dict[str, pd.DataFrame]]:
    frame = load_horizon(core_db, int(horizon), cfg, manifest)
    folds = build_purged_day_folds(
        frame,
        n_folds=int(cfg["outer_folds"]),
        initial_fraction=float(cfg["initial_fraction"]),
    )
    quantiles = tuple(float(q) for q in cfg["quantiles"])
    own_features = list(cfg["frozen_own_features"])
    preserved_features = list(cfg["capacity_placebo"]["preserved_aligned_features"])
    placebo_enabled = int(horizon) in {
        int(x) for x in cfg["capacity_placebo"]["enabled_horizons_sessions"]
    }
    placebo_seeds = [
        int(x) for x in cfg["capacity_placebo"]["seeds"]
    ] if placebo_enabled else []

    fold_results = []
    oos_parts = []
    for fold in folds:
        train = frame.loc[list(fold.train_index)].copy()
        test_columns = [
            "state_id",
            "asset_id",
            "ticker",
            "sector",
            "origin_trading_day",
            "target_trading_day",
            "return_pct",
            *own_features,
        ]
        test = frame.loc[
            list(fold.test_index),
            list(dict.fromkeys(test_columns)),
        ].copy()

        candidate, candidate_diagnostics = fit_raw_model_bundle(
            train,
            test,
            own_features,
            cfg,
        )
        reference = fit_raw_vol63_bundle(train, test, cfg)
        bundles: dict[str, Mapping[str, Any]] = {
            "hgb_own_state_raw": candidate,
            "vol63_raw": reference,
        }
        placebo_diagnostics = {}
        for seed in placebo_seeds:
            placebo_train, permutation_audit = make_capacity_placebo_train(
                train,
                own_features,
                preserved_features,
                seed,
            )
            placebo_bundle, model_diagnostics = fit_raw_model_bundle(
                placebo_train,
                test,
                own_features,
                cfg,
            )
            name = f"hgb_own_state_placebo_seed_{seed}"
            bundles[name] = placebo_bundle
            placebo_diagnostics[str(seed)] = {
                "permutation_audit": permutation_audit,
                "model_diagnostics": model_diagnostics,
            }

        actual = test["return_pct"].to_numpy(float)
        metrics = {
            name: distribution_metrics(
                actual,
                bundle["quantiles"],
                bundle["probability_positive"],
            )
            for name, bundle in bundles.items()
        }
        for name, bundle in bundles.items():
            _store_bundle(test, name, bundle)
        test["fold_id"] = int(fold.fold_id)
        oos_parts.append(test)

        fold_reference_loss = mean_pinball_rows(actual, reference["quantiles"])
        fold_candidate_loss = mean_pinball_rows(actual, candidate["quantiles"])
        fold_daily = daily_loss_comparison(
            test["origin_trading_day"],
            fold_reference_loss,
            fold_candidate_loss,
        )
        fold_results.append({
            "fold_id": int(fold.fold_id),
            "first_test_day": fold.first_test_day,
            "last_test_day": fold.last_test_day,
            "train_rows": int(len(train)),
            "train_origin_days": int(train["origin_trading_day"].nunique()),
            "test_rows": int(len(test)),
            "test_origin_days": int(test["origin_trading_day"].nunique()),
            "primary_origin_day_equal_delta_pct": float(
                fold_daily["loss_delta_baseline_minus_candidate"].mean()
            ),
            "candidate_diagnostics": candidate_diagnostics,
            "capacity_placebo_diagnostics": placebo_diagnostics,
            "metrics": metrics,
        })

    oos = pd.concat(oos_parts, ignore_index=True)
    actual = oos["return_pct"].to_numpy(float)
    candidate = _bundle_from_columns(oos, "hgb_own_state_raw", quantiles)
    reference = _bundle_from_columns(oos, "vol63_raw", quantiles)
    candidate_metrics = distribution_metrics(
        actual,
        candidate["quantiles"],
        candidate["probability_positive"],
    )
    reference_metrics = distribution_metrics(
        actual,
        reference["quantiles"],
        reference["probability_positive"],
    )
    primary, primary_daily = _comparison(oos, actual, reference, candidate, cfg)
    per_quantile = _per_quantile_comparison(oos, actual, reference, candidate)

    tables: dict[str, pd.DataFrame] = {"primary_daily_losses": primary_daily}
    placebo_seed_comparisons = {}
    placebo_losses = []
    placebo_metrics = {}
    for seed in placebo_seeds:
        name = f"hgb_own_state_placebo_seed_{seed}"
        bundle = _bundle_from_columns(oos, name, quantiles)
        comparison, daily = _comparison(oos, actual, bundle, candidate, cfg)
        placebo_seed_comparisons[str(seed)] = comparison
        placebo_metrics[str(seed)] = distribution_metrics(
            actual,
            bundle["quantiles"],
            bundle["probability_positive"],
        )
        placebo_losses.append(mean_pinball_rows(actual, bundle["quantiles"]))
        tables[f"candidate_vs_placebo_seed_{seed}_daily_losses"] = daily

    placebo_mean = None
    if placebo_losses:
        mean_placebo_loss = np.mean(np.column_stack(placebo_losses), axis=1)
        candidate_loss = mean_pinball_rows(actual, candidate["quantiles"])
        placebo_mean, placebo_mean_daily = _loss_comparison(
            oos["origin_trading_day"],
            mean_placebo_loss,
            candidate_loss,
            cfg,
        )
        tables["candidate_vs_mean_placebo_daily_losses"] = placebo_mean_daily

    row_delta = (
        mean_pinball_rows(actual, reference["quantiles"])
        - mean_pinball_rows(actual, candidate["quantiles"])
    )
    fold_table = _group_delta_table(oos, row_delta, "fold_id")
    asset_table = _group_delta_table(oos, row_delta, "asset_id")
    sector_table = _group_delta_table(oos, row_delta, "sector")
    year_frame = oos.copy()
    year_frame["origin_year"] = year_frame["origin_trading_day"].astype(str).str[:4]
    year_table = _group_delta_table(year_frame, row_delta, "origin_year")
    tables.update({
        "fold_deltas": fold_table,
        "asset_deltas": asset_table,
        "sector_deltas": sector_table,
        "year_deltas": year_table,
    })

    gate = evaluate_horizon_gate(
        int(horizon),
        primary,
        candidate_metrics,
        reference_metrics,
        per_quantile,
        fold_table,
        placebo_mean,
        placebo_seed_comparisons,
        cfg,
    )
    report = {
        "benchmark_version": cfg["version"],
        "model_version": cfg["model_version"],
        "source_v008_version": cfg["source_v008_version"],
        "dataset_contract": cfg["dataset_contract"],
        "market_feature_version": cfg["market_feature_version"],
        "label_version": cfg["label_version"],
        "horizon_sessions": int(horizon),
        "primary_horizon_sessions": int(cfg["primary_horizon_sessions"]),
        "oos_rows": int(len(oos)),
        "oos_assets": int(oos["asset_id"].nunique()),
        "oos_origin_days": int(oos["origin_trading_day"].nunique()),
        "oos_first_day": str(oos["origin_trading_day"].min()),
        "oos_last_day": str(oos["origin_trading_day"].max()),
        "feature_manifest_sha256": _json_sha(manifest),
        "frozen_own_features": own_features,
        "primary_reference": cfg["primary_reference"],
        "primary_candidate": cfg["primary_candidate"],
        "post_model_calibration": "none",
        "fixed_model_profile": cfg["fixed_model_profile"],
        "fold_contract": fold_summary(folds),
        "fold_results": fold_results,
        "pooled_metrics": {
            "hgb_own_state_raw": candidate_metrics,
            "vol63_raw": reference_metrics,
            "capacity_placebos": placebo_metrics,
        },
        "primary_comparison": primary,
        "per_quantile_primary_comparison": per_quantile,
        "capacity_placebo": {
            "enabled": placebo_enabled,
            "seeds": placebo_seeds,
            "candidate_vs_each_placebo": placebo_seed_comparisons,
            "candidate_vs_mean_placebo": placebo_mean,
        },
        "concentration_diagnostics": {
            "fold": _group_summary(fold_table),
            "asset": _group_summary(asset_table),
            "sector": _group_summary(sector_table),
            "year": _group_summary(year_table),
        },
        "horizon_gate": gate,
        "interpretation_contract": {
            "primary_question": "does the frozen own price/volume state improve H1 standardized-return quantiles beyond raw vol63 without recent recalibration?",
            "secondary_horizons_cannot_rescue_h1": True,
            "capacity_control_preserves_aligned_scale_and_destroys_incremental_asset_state_alignment": True,
            "pass_is_developmental_on_reused_history": True,
            "fresh_temporal_confirmation_required_for_promotion": True,
            "failure_closes_current_endogenous_price_volume_engineering_branch": True,
        },
    }
    return report, oos, tables
