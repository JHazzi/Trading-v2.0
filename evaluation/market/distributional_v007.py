from __future__ import annotations

from pathlib import Path
from typing import Any

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
from models.market.distributional_v007_adaptive_tail import (
    DEFAULT_CONFIG,
    DEFAULT_CORE_DB,
    fit_anchor,
    fit_predict_controls,
    load_config,
    load_horizon,
    predict_adaptive_distribution,
    select_nested_parameters,
)

MODEL_NAMES = (
    "train_empirical",
    "asset_empirical",
    "vol20_scaled_empirical",
    "vol63_scaled_empirical",
    "adaptive_asymmetric_asset_scale",
)


def _bundle_columns(
    frame: pd.DataFrame,
    model_name: str,
    bundle: dict[str, object],
) -> None:
    for q, values in bundle["quantiles"].items():
        frame[f"{model_name}_{quantile_name(float(q))}"] = np.asarray(
            values, dtype="float32"
        )
    frame[f"{model_name}_prob_positive"] = np.asarray(
        bundle["probability_positive"], dtype="float32"
    )


def _bundle_from_columns(
    frame: pd.DataFrame,
    model_name: str,
    quantiles: tuple[float, ...],
) -> dict[str, object]:
    return {
        "quantiles": {
            q: frame[f"{model_name}_{quantile_name(q)}"].to_numpy(float)
            for q in quantiles
        },
        "probability_positive": frame[
            f"{model_name}_prob_positive"
        ].to_numpy(float),
    }


def mean_absolute_quantile_calibration_error(metrics: dict[str, object]) -> float:
    values = [
        abs(float(item["calibration_error"]))
        for item in metrics["per_quantile"].values()
    ]
    return float(np.mean(values))


def _comparison(
    oos: pd.DataFrame,
    actual: np.ndarray,
    baseline: dict[str, object],
    candidate: dict[str, object],
    cfg: dict[str, Any],
) -> tuple[dict[str, Any], pd.DataFrame]:
    base_loss = mean_pinball_rows(actual, baseline["quantiles"])
    cand_loss = mean_pinball_rows(actual, candidate["quantiles"])
    daily = daily_loss_comparison(
        oos["origin_trading_day"], base_loss, cand_loss
    )
    bootstrap = {
        str(block): moving_block_bootstrap_daily_loss(
            daily,
            block_length=int(block),
            reps=int(cfg["bootstrap_reps"]),
            seed=int(cfg["bootstrap_seed"]),
        )
        for block in cfg["moving_block_lengths_origin_days"]
    }
    return {
        "row_weighted_delta_pct": float(np.mean(base_loss - cand_loss)),
        "origin_day_equal_weight_delta_pct": float(
            daily["loss_delta_baseline_minus_candidate"].mean()
        ),
        "positive_delta_means_candidate_lower_loss": True,
        "moving_block_bootstrap": bootstrap,
    }, daily


def _tail_comparisons(
    oos: pd.DataFrame,
    actual: np.ndarray,
    reference: dict[str, object],
    candidate: dict[str, object],
    cfg: dict[str, Any],
) -> dict[str, Any]:
    result = {}
    for q in tuple(float(x) for x in cfg["quantiles"]):
        base = pinball_rows(actual, reference["quantiles"][q], q)
        cand = pinball_rows(actual, candidate["quantiles"][q], q)
        daily = daily_loss_comparison(oos["origin_trading_day"], base, cand)
        result[quantile_name(q)] = {
            "quantile": q,
            "origin_day_equal_weight_delta_pct": float(
                daily["loss_delta_baseline_minus_candidate"].mean()
            ),
            "moving_block_bootstrap_10": moving_block_bootstrap_daily_loss(
                daily,
                block_length=10,
                reps=1000,
                seed=int(cfg["bootstrap_seed"]),
            ),
        }
    return result


def _horizon_gate(
    primary: dict[str, Any],
    candidate_metrics: dict[str, object],
    reference_metrics: dict[str, object],
) -> dict[str, Any]:
    ci10 = primary["moving_block_bootstrap"]["10"]["ci95"]
    point = float(primary["origin_day_equal_weight_delta_pct"])
    cand_cal = mean_absolute_quantile_calibration_error(candidate_metrics)
    ref_cal = mean_absolute_quantile_calibration_error(reference_metrics)
    if float(ci10[0]) > 0.0 and cand_cal <= ref_cal:
        status = "PASS_STRONG"
    elif float(ci10[0]) > 0.0:
        status = "PASS_SCORE_ONLY_CALIBRATION_WORSE"
    elif point > 0.0 and float(ci10[1]) >= 0.0:
        status = "INCONCLUSIVE_POSITIVE_POINT"
    elif float(ci10[1]) < 0.0 or point <= 0.0:
        status = "FAIL"
    else:
        status = "INCONCLUSIVE"
    return {
        "status": status,
        "primary_point_delta_pct": point,
        "primary_block10_ci95": [float(ci10[0]), float(ci10[1])],
        "candidate_mean_abs_quantile_calibration_error": cand_cal,
        "reference_mean_abs_quantile_calibration_error": ref_cal,
        "calibration_not_worse": bool(cand_cal <= ref_cal),
        "claim": (
            "horizon-specific developmental evidence only; V007 uses a hypothesis informed "
            "by V006.1 and is not an independent prospective confirmation"
        ),
    }


def run_horizon(
    core_db: Path,
    horizon: int,
    cfg: dict[str, Any],
) -> tuple[dict[str, Any], pd.DataFrame, dict[str, pd.DataFrame]]:
    frame = load_horizon(core_db, int(horizon), cfg)
    folds = build_purged_day_folds(
        frame,
        n_folds=int(cfg["outer_folds"]),
        initial_fraction=float(cfg["initial_fraction"]),
    )
    quantiles = tuple(float(q) for q in cfg["quantiles"])
    fold_results: list[dict[str, Any]] = []
    oos_parts: list[pd.DataFrame] = []
    selection_tables: dict[str, pd.DataFrame] = {}

    for fold in folds:
        train = frame.loc[list(fold.train_index)].copy()
        test = frame.loc[list(fold.test_index), [
            "state_id",
            "asset_id",
            "ticker",
            "sector",
            "origin_trading_day",
            "target_trading_day",
            "return_pct",
            "asset_vol_20d_pct",
            "asset_vol_63d_pct",
        ]].copy()
        lower, upper, selection_meta, grids = select_nested_parameters(train, cfg)
        anchor = fit_anchor(train, quantiles)
        controls = fit_predict_controls(train, test, cfg)
        candidate = predict_adaptive_distribution(
            test, anchor, lower, upper, cfg
        )
        bundles = {**controls, cfg["primary_candidate"]: candidate}
        metrics = {
            name: distribution_metrics(
                test["return_pct"].to_numpy(float),
                bundle["quantiles"],
                bundle["probability_positive"],
            )
            for name, bundle in bundles.items()
        }
        for name, bundle in bundles.items():
            _bundle_columns(test, name, bundle)
        test["fold_id"] = int(fold.fold_id)
        oos_parts.append(test)

        base_loss = mean_pinball_rows(
            test["return_pct"].to_numpy(float),
            bundles[cfg["primary_reference"]]["quantiles"],
        )
        cand_loss = mean_pinball_rows(
            test["return_pct"].to_numpy(float), candidate["quantiles"]
        )
        daily = daily_loss_comparison(
            test["origin_trading_day"], base_loss, cand_loss
        )
        fold_results.append({
            "fold_id": int(fold.fold_id),
            "first_test_day": fold.first_test_day,
            "last_test_day": fold.last_test_day,
            "train_rows": int(len(train)),
            "test_rows": int(len(test)),
            "nested_selection": selection_meta,
            "metrics": metrics,
            "primary_comparison": {
                "baseline": cfg["primary_reference"],
                "candidate": cfg["primary_candidate"],
                "origin_day_equal_weight_delta_pct": float(
                    daily["loss_delta_baseline_minus_candidate"].mean()
                ),
                "row_weighted_delta_pct": float(np.mean(base_loss - cand_loss)),
                "positive_delta_means_candidate_lower_loss": True,
            },
        })
        for side, table in grids.items():
            selection_tables[f"fold{fold.fold_id}_{side}"] = table

    oos = pd.concat(oos_parts, ignore_index=True)
    actual = oos["return_pct"].to_numpy(float)
    bundles = {
        name: _bundle_from_columns(oos, name, quantiles)
        for name in MODEL_NAMES
    }
    pooled_metrics = {
        name: distribution_metrics(
            actual, bundle["quantiles"], bundle["probability_positive"]
        )
        for name, bundle in bundles.items()
    }

    primary, primary_daily = _comparison(
        oos,
        actual,
        bundles[cfg["primary_reference"]],
        bundles[cfg["primary_candidate"]],
        cfg,
    )
    comparisons: dict[str, Any] = {}
    comparison_daily_tables = {"primary_daily_losses": primary_daily}
    for reference in cfg["secondary_references"]:
        comp, daily = _comparison(
            oos,
            actual,
            bundles[reference],
            bundles[cfg["primary_candidate"]],
            cfg,
        )
        comparisons[reference] = comp
        comparison_daily_tables[f"vs_{reference}_daily_losses"] = daily

    candidate_metrics = pooled_metrics[cfg["primary_candidate"]]
    reference_metrics = pooled_metrics[cfg["primary_reference"]]
    report = {
        "benchmark_version": cfg["version"],
        "model_version": cfg["model_version"],
        "dataset_contract": cfg["dataset_contract"],
        "market_feature_version": cfg["market_feature_version"],
        "label_version": cfg["label_version"],
        "horizon_sessions": int(horizon),
        "target": cfg["target"],
        "oos_rows": int(len(oos)),
        "oos_assets": int(oos["asset_id"].nunique()),
        "oos_origin_days": int(oos["origin_trading_day"].nunique()),
        "oos_first_day": str(oos["origin_trading_day"].min()),
        "oos_last_day": str(oos["origin_trading_day"].max()),
        "fold_contract": fold_summary(folds),
        "fold_results": fold_results,
        "pooled_metrics": pooled_metrics,
        "primary_comparison": {
            "baseline": cfg["primary_reference"],
            "candidate": cfg["primary_candidate"],
            **primary,
        },
        "secondary_comparisons": comparisons,
        "tail_specific_vs_primary_reference": _tail_comparisons(
            oos,
            actual,
            bundles[cfg["primary_reference"]],
            bundles[cfg["primary_candidate"]],
            cfg,
        ),
        "horizon_gate": _horizon_gate(
            primary, candidate_metrics, reference_metrics
        ),
        "scientific_contract": {
            "nested_temporal_selection": True,
            "outer_test_design_reused_from_v003": True,
            "target_end_before_outer_test_origin": True,
            "inner_target_end_before_inner_validation_origin": True,
            "primary_inference_unit": "origin_trading_day",
            "test_outcomes_used_for_prediction": False,
            "strict_historical_pit": False,
            "current_cohort_not_survivorship_free": True,
            "location_learning": False,
            "event_graph_macro_external_features": False,
            "broker_cost_in_training": False,
            "terminal_return_not_joint_path": True,
            "developmental_not_prospective_confirmation": True,
            "production_ready": False,
        },
    }
    tables = {**selection_tables, **comparison_daily_tables, "oos_predictions": oos}
    return report, oos, tables
