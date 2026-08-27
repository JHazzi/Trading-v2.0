from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from evaluation.market.daily_v003_benchmark import (
    build_purged_day_folds,
    fold_summary,
)
from evaluation.market.distributional_v006 import (
    daily_loss_comparison,
    distribution_metrics,
    mean_pinball_rows,
    moving_block_bootstrap_daily_loss,
    quantile_name,
)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CORE_DB = ROOT / "data" / "processed" / "market_daily_v003_core.db"
DEFAULT_CONFIG = ROOT / "config" / "market_brain_distributional_v006.json"

REQUIRED_QUANTILES = (0.05, 0.25, 0.5, 0.75, 0.95)
MODEL_NAMES = (
    "train_empirical",
    "volatility_scaled_empirical",
    "asset_empirical",
)


def load_config(path: Path = DEFAULT_CONFIG) -> dict[str, Any]:
    cfg = json.loads(path.read_text(encoding="utf-8"))
    if cfg["model_version"] != (
        "market_brain_distributional_v006_empirical_baselines_v001"
    ):
        raise ValueError("unexpected model_version")
    if cfg["market_feature_version"] != "market_daily_state_v003_core":
        raise ValueError("unexpected market feature version")
    if cfg["label_version"] != "market_daily_reaction_v003_core":
        raise ValueError("unexpected label version")
    if cfg["target"] != "return_pct":
        raise ValueError("V006 foundation target must be terminal return")
    if tuple(float(x) for x in cfg["quantiles"]) != REQUIRED_QUANTILES:
        raise ValueError("V006 quantile grid changed")
    if cfg["primary_baseline"] != "train_empirical":
        raise ValueError("primary baseline changed")
    if cfg["primary_candidate"] != "volatility_scaled_empirical":
        raise ValueError("primary candidate changed")
    if cfg["secondary_reference"] != "asset_empirical":
        raise ValueError("secondary reference changed")
    if cfg["scale_feature"] != "asset_vol_20d_pct":
        raise ValueError("scale feature changed")
    if cfg["nonpositive_scale_policy"] != "global_empirical_fallback":
        raise ValueError("nonpositive scale policy changed")
    if cfg["primary_score"] != (
        "origin_day_equal_weight_mean_pinball_loss"
    ):
        raise ValueError("primary score changed")
    if cfg.get("broker_cost_used_for_training") is not False:
        raise ValueError("broker costs belong downstream")
    for key in (
        "external_proxies_added",
        "event_features_added",
        "graph_features_added",
        "macro_features_added",
    ):
        if cfg.get(key) is not False:
            raise ValueError(f"deferred information enabled: {key}")
    if cfg.get("strict_historical_pit") is not False:
        raise ValueError("historical Core V003 is not strict PIT")
    if cfg.get("no_hyperparameter_tuning") is not True:
        raise ValueError("foundation must remain tuning-free")
    if cfg.get("no_best_horizon_selection") is not True:
        raise ValueError("all horizons must be reported")
    return cfg


def load_horizon(
    core_db: Path,
    horizon: int,
    cfg: dict[str, Any],
) -> pd.DataFrame:
    with sqlite3.connect(core_db) as conn:
        state_columns = {
            str(row[1]) for row in conn.execute(
                "PRAGMA table_info(market_daily_v003_states)"
            )
        }
        label_columns = {
            str(row[1]) for row in conn.execute(
                "PRAGMA table_info(market_daily_v003_labels)"
            )
        }
        required_state = {
            "state_id",
            "asset_id",
            "ticker",
            "sector",
            "trading_day",
            "feature_version",
            "state_point_in_time_verified",
            str(cfg["scale_feature"]),
        }
        required_label = {
            "state_id",
            "origin_trading_day",
            "target_trading_day",
            "horizon_sessions",
            "return_pct",
            "corporate_action_overlap",
            "label_status",
            "label_version",
        }
        missing = sorted(
            (required_state - state_columns) | (required_label - label_columns)
        )
        if missing:
            raise RuntimeError(f"Core V003 columns missing: {missing}")

        frame = pd.read_sql_query(
            f"""
            SELECT
              s.state_id,
              s.asset_id,
              s.ticker,
              s.sector,
              l.origin_trading_day,
              l.target_trading_day,
              l.return_pct,
              s.{cfg["scale_feature"]} AS scale_value,
              s.state_point_in_time_verified,
              l.corporate_action_overlap
            FROM market_daily_v003_labels l
            JOIN market_daily_v003_states s ON s.state_id=l.state_id
            WHERE l.horizon_sessions=?
              AND l.label_status='usable'
              AND l.label_version=?
              AND s.feature_version=?
            ORDER BY l.origin_trading_day,s.asset_id
            """,
            conn,
            params=(
                int(horizon),
                str(cfg["label_version"]),
                str(cfg["market_feature_version"]),
            ),
        )

    if frame.empty:
        raise RuntimeError(f"no usable rows for H{horizon}")
    frame.index = np.arange(len(frame), dtype=int)
    frame["asset_id"] = frame["asset_id"].astype("int32")
    for column in ("return_pct", "scale_value"):
        frame[column] = pd.to_numeric(
            frame[column], errors="raise"
        ).astype("float64")
    if not np.isfinite(
        frame[["return_pct", "scale_value"]].to_numpy(float)
    ).all():
        raise RuntimeError("nonfinite V006 benchmark data")
    if (frame["target_trading_day"] <= frame["origin_trading_day"]).any():
        raise RuntimeError("invalid target clock")
    if (frame["corporate_action_overlap"].astype(int) != 0).any():
        raise RuntimeError("usable labels contain corporate-action overlap")
    return frame


def _global_distribution(
    y: np.ndarray,
    quantiles: tuple[float, ...],
    rows: int,
) -> dict[str, object]:
    values = np.asarray(y, dtype=float)
    q_values = np.quantile(values, quantiles, method="linear")
    return {
        "quantiles": {
            q: np.full(rows, float(value), dtype=float)
            for q, value in zip(quantiles, q_values)
        },
        "probability_positive": np.full(
            rows, float(np.mean(values > 0.0)), dtype=float
        ),
    }


def fit_predict_baselines(
    train: pd.DataFrame,
    test: pd.DataFrame,
    cfg: dict[str, Any],
) -> dict[str, dict[str, object]]:
    quantiles = tuple(float(q) for q in cfg["quantiles"])
    y = train["return_pct"].to_numpy(float)
    global_prediction = _global_distribution(y, quantiles, len(test))
    predictions: dict[str, dict[str, object]] = {
        "train_empirical": global_prediction
    }

    asset_quantiles: dict[float, np.ndarray] = {}
    for q in quantiles:
        values = train.groupby("asset_id", sort=False)["return_pct"].quantile(q)
        fallback = float(np.quantile(y, q, method="linear"))
        asset_quantiles[q] = (
            test["asset_id"].map(values).fillna(fallback).to_numpy(float)
        )
    asset_positive = (
        train.assign(_positive=train["return_pct"] > 0.0)
        .groupby("asset_id", sort=False)["_positive"]
        .mean()
    )
    global_positive = float(np.mean(y > 0.0))
    predictions["asset_empirical"] = {
        "quantiles": asset_quantiles,
        "probability_positive": (
            test["asset_id"].map(asset_positive)
            .fillna(global_positive)
            .to_numpy(float)
        ),
    }

    train_scale = train["scale_value"].to_numpy(float)
    valid_scale = train_scale > 0.0
    if int(np.sum(valid_scale)) < len(quantiles) + 1:
        raise RuntimeError("insufficient positive volatility-scale support")
    location = float(np.median(y))
    standardized = (y[valid_scale] - location) / train_scale[valid_scale]
    standardized_sorted = np.sort(standardized)
    standardized_quantiles = np.quantile(
        standardized, quantiles, method="linear"
    )

    test_scale = test["scale_value"].to_numpy(float)
    usable_test_scale = test_scale > 0.0
    scaled_quantiles: dict[float, np.ndarray] = {}
    for index, q in enumerate(quantiles):
        fallback = np.asarray(
            global_prediction["quantiles"][q], dtype=float
        ).copy()
        fallback[usable_test_scale] = (
            location
            + float(standardized_quantiles[index])
            * test_scale[usable_test_scale]
        )
        scaled_quantiles[q] = fallback

    scaled_positive = np.asarray(
        global_prediction["probability_positive"], dtype=float
    ).copy()
    thresholds = (
        (0.0 - location) / test_scale[usable_test_scale]
    )
    right = np.searchsorted(
        standardized_sorted, thresholds, side="right"
    )
    scaled_positive[usable_test_scale] = (
        len(standardized_sorted) - right
    ) / float(len(standardized_sorted))
    predictions["volatility_scaled_empirical"] = {
        "quantiles": scaled_quantiles,
        "probability_positive": scaled_positive,
    }
    return predictions


def _metrics_for_bundle(
    actual: np.ndarray,
    bundle: dict[str, object],
) -> dict[str, object]:
    return distribution_metrics(
        actual,
        bundle["quantiles"],
        bundle["probability_positive"],
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
            q: frame[
                f"{model_name}_{quantile_name(q)}"
            ].to_numpy(float)
            for q in quantiles
        },
        "probability_positive": frame[
            f"{model_name}_prob_positive"
        ].to_numpy(float),
    }


def run_horizon(
    core_db: Path,
    horizon: int,
    cfg: dict[str, Any],
) -> tuple[dict[str, Any], pd.DataFrame]:
    frame = load_horizon(core_db, horizon, cfg)
    folds = build_purged_day_folds(
        frame,
        n_folds=int(cfg["outer_folds"]),
        initial_fraction=float(cfg["initial_fraction"]),
    )
    quantiles = tuple(float(q) for q in cfg["quantiles"])
    fold_results = []
    oos_parts = []

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
            "scale_value",
        ]].copy()
        prediction_input = test[[
            "asset_id",
            "scale_value",
        ]].copy()
        bundles = fit_predict_baselines(train, prediction_input, cfg)
        metrics_by_model = {}
        for model_name in MODEL_NAMES:
            bundle = bundles[model_name]
            metrics_by_model[model_name] = _metrics_for_bundle(
                test["return_pct"].to_numpy(float), bundle
            )
            _bundle_columns(test, model_name, bundle)
        test["fold_id"] = int(fold.fold_id)
        oos_parts.append(test)

        base_loss = mean_pinball_rows(
            test["return_pct"].to_numpy(float),
            bundles["train_empirical"]["quantiles"],
        )
        candidate_loss = mean_pinball_rows(
            test["return_pct"].to_numpy(float),
            bundles["volatility_scaled_empirical"]["quantiles"],
        )
        fold_daily = daily_loss_comparison(
            test["origin_trading_day"], base_loss, candidate_loss
        )
        fold_results.append({
            "fold_id": int(fold.fold_id),
            "first_test_day": fold.first_test_day,
            "last_test_day": fold.last_test_day,
            "train_rows": int(len(train)),
            "test_rows": int(len(test)),
            "metrics": metrics_by_model,
            "primary_comparison": {
                "row_weighted_delta_pct": float(
                    np.mean(base_loss - candidate_loss)
                ),
                "origin_day_equal_weight_delta_pct": float(
                    fold_daily[
                        "loss_delta_baseline_minus_candidate"
                    ].mean()
                ),
                "positive_delta_means_candidate_lower_loss": True,
            },
        })

    oos = pd.concat(oos_parts, ignore_index=True)
    actual = oos["return_pct"].to_numpy(float)
    pooled_metrics = {}
    bundles = {}
    for model_name in MODEL_NAMES:
        bundle = _bundle_from_columns(oos, model_name, quantiles)
        bundles[model_name] = bundle
        pooled_metrics[model_name] = _metrics_for_bundle(actual, bundle)

    base_loss = mean_pinball_rows(
        actual, bundles["train_empirical"]["quantiles"]
    )
    candidate_loss = mean_pinball_rows(
        actual, bundles["volatility_scaled_empirical"]["quantiles"]
    )
    asset_loss = mean_pinball_rows(
        actual, bundles["asset_empirical"]["quantiles"]
    )
    primary_daily = daily_loss_comparison(
        oos["origin_trading_day"], base_loss, candidate_loss
    )
    asset_daily = daily_loss_comparison(
        oos["origin_trading_day"], base_loss, asset_loss
    )

    bootstrap = {
        str(block): moving_block_bootstrap_daily_loss(
            primary_daily,
            block_length=int(block),
            reps=int(cfg["bootstrap_reps"]),
            seed=int(cfg["bootstrap_seed"]),
        )
        for block in cfg["moving_block_lengths_origin_days"]
    }

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
            "baseline": "train_empirical",
            "candidate": "volatility_scaled_empirical",
            "row_weighted_delta_pct": float(
                np.mean(base_loss - candidate_loss)
            ),
            "origin_day_equal_weight_delta_pct": float(
                primary_daily[
                    "loss_delta_baseline_minus_candidate"
                ].mean()
            ),
            "positive_delta_means_candidate_lower_loss": True,
        },
        "secondary_asset_reference": {
            "baseline": "train_empirical",
            "candidate": "asset_empirical",
            "row_weighted_delta_pct": float(
                np.mean(base_loss - asset_loss)
            ),
            "origin_day_equal_weight_delta_pct": float(
                asset_daily[
                    "loss_delta_baseline_minus_candidate"
                ].mean()
            ),
            "claim_boundary": "secondary reference; cannot rescue primary",
        },
        "primary_moving_block_bootstrap": bootstrap,
        "scientific_contract": {
            "all_horizons_reported": True,
            "outer_test_design_reused_from_v003": True,
            "target_end_before_test_origin": True,
            "primary_inference_unit": "origin_trading_day",
            "test_outcomes_used_for_prediction": False,
            "strict_historical_pit": False,
            "current_cohort_not_survivorship_free": True,
            "broker_cost_in_training": False,
            "event_or_graph_features": False,
            "production_ready": False,
        },
    }
    return report, primary_daily
