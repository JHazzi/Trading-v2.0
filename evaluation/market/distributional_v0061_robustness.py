from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Mapping

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
    pinball_rows,
    quantile_name,
)
from models.market.distributional_v006_baselines import fit_predict_baselines

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CORE_DB = ROOT / "data" / "processed" / "market_daily_v003_core.db"
DEFAULT_CONFIG = ROOT / "config" / "market_brain_distributional_v0061.json"
DEFAULT_SOURCE_REPORT_DIR = (
    ROOT / "reports" / "market_brain_distributional_v006" / "empirical_baseline_v001"
)

PRIMARY_MODELS = (
    "train_empirical",
    "volatility_scaled_empirical",
    "asset_empirical",
)


def load_config(path: Path = DEFAULT_CONFIG) -> dict[str, Any]:
    cfg = json.loads(path.read_text(encoding="utf-8"))
    if cfg["version"] != "market_brain_distributional_v0061_robustness_v001":
        raise ValueError("unexpected V006.1 version")
    if cfg["source_benchmark_version"] != "market_brain_distributional_v006_baseline_v001":
        raise ValueError("V006 source benchmark changed")
    if cfg["source_model_version"] != "market_brain_distributional_v006_empirical_baselines_v001":
        raise ValueError("V006 source model changed")
    if cfg["market_feature_version"] != "market_daily_state_v003_core":
        raise ValueError("market feature version changed")
    if cfg["label_version"] != "market_daily_reaction_v003_core":
        raise ValueError("label version changed")
    if cfg["target"] != "return_pct":
        raise ValueError("V006.1 robustness must retain terminal return target")
    if tuple(int(x) for x in cfg["horizons_sessions"]) != (1, 3, 5, 10):
        raise ValueError("all four frozen horizons are required")
    if tuple(float(x) for x in cfg["quantiles"]) != (0.05, 0.25, 0.5, 0.75, 0.95):
        raise ValueError("V006 quantile grid changed")
    if cfg["primary_baseline"] != "train_empirical":
        raise ValueError("V006 primary baseline changed")
    if cfg["primary_candidate"] != "volatility_scaled_empirical":
        raise ValueError("V006 primary candidate changed")
    if cfg["secondary_reference"] != "asset_empirical":
        raise ValueError("V006 secondary reference changed")
    if cfg["primary_scale_feature"] != "asset_vol_20d_pct":
        raise ValueError("V006 primary scale feature changed")
    if tuple(cfg["alternative_scale_features"]) != (
        "asset_vol_5d_pct",
        "asset_vol_63d_pct",
    ):
        raise ValueError("alternative scales changed")
    if cfg.get("no_new_model_training") is not True:
        raise ValueError("V006.1 is diagnostics only")
    if cfg.get("no_posthoc_candidate_selection") is not True:
        raise ValueError("post-hoc candidate selection is forbidden")
    for key in (
        "no_event_features",
        "no_graph_features",
        "no_macro_features",
        "no_external_proxy_features",
        "no_best_horizon_selection",
    ):
        if cfg.get(key) is not True:
            raise ValueError(f"scientific boundary disabled: {key}")
    if cfg.get("strict_historical_pit") is not False:
        raise ValueError("historical Core V003 is not strict PIT")
    regime_q = tuple(float(x) for x in cfg["volatility_regime_train_quantiles"])
    if len(regime_q) != 2 or not 0.0 < regime_q[0] < regime_q[1] < 1.0:
        raise ValueError("invalid volatility-regime quantiles")
    if int(cfg["calibration_block_origin_days"]) < 20:
        raise ValueError("calibration block is implausibly short")
    return cfg


def load_horizon_frame(
    core_db: Path,
    horizon: int,
    cfg: Mapping[str, Any],
) -> pd.DataFrame:
    scale_features = [
        str(cfg["primary_scale_feature"]),
        *[str(x) for x in cfg["alternative_scale_features"]],
    ]
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
            *scale_features,
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
        missing = sorted((required_state - state_columns) | (required_label - label_columns))
        if missing:
            raise RuntimeError(f"V006.1 source columns missing: {missing}")
        scale_sql = ",\n              ".join(f"s.{col}" for col in scale_features)
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
              {scale_sql},
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
    numeric = ["return_pct", *scale_features]
    for column in numeric:
        frame[column] = pd.to_numeric(frame[column], errors="raise").astype("float64")
    if not np.isfinite(frame[numeric].to_numpy(float)).all():
        raise RuntimeError("nonfinite V006.1 benchmark data")
    if (frame["target_trading_day"] <= frame["origin_trading_day"]).any():
        raise RuntimeError("invalid target clock")
    if (frame["corporate_action_overlap"].astype(int) != 0).any():
        raise RuntimeError("usable labels contain corporate-action overlap")
    if frame.duplicated(["asset_id", "origin_trading_day"]).any():
        raise RuntimeError("duplicate asset/origin rows violate concentration algebra")
    return frame


def _scaled_empirical_bundle(
    train_y: np.ndarray,
    train_scale: np.ndarray,
    test_scale: np.ndarray,
    quantiles: Iterable[float],
) -> dict[str, object]:
    """Exact V006 scale logic for a predeclared alternative causal scale."""
    y = np.asarray(train_y, dtype=float)
    tr_scale = np.asarray(train_scale, dtype=float)
    te_scale = np.asarray(test_scale, dtype=float)
    qs = tuple(float(q) for q in quantiles)
    location = float(np.median(y))
    valid = tr_scale > 0.0
    if int(np.sum(valid)) < len(qs) + 1:
        raise RuntimeError("insufficient positive alternative-scale support")
    standardized = (y[valid] - location) / tr_scale[valid]
    standardized_sorted = np.sort(standardized)
    standardized_quantiles = np.quantile(standardized, qs, method="linear")
    global_quantiles = np.quantile(y, qs, method="linear")
    usable = te_scale > 0.0
    predictions: dict[float, np.ndarray] = {}
    for index, q in enumerate(qs):
        values = np.full(len(te_scale), float(global_quantiles[index]), dtype=float)
        values[usable] = location + float(standardized_quantiles[index]) * te_scale[usable]
        predictions[q] = values
    probability = np.full(len(te_scale), float(np.mean(y > 0.0)), dtype=float)
    thresholds = (0.0 - location) / te_scale[usable]
    right = np.searchsorted(standardized_sorted, thresholds, side="right")
    probability[usable] = (len(standardized_sorted) - right) / float(len(standardized_sorted))
    return {"quantiles": predictions, "probability_positive": probability}


def _store_bundle(frame: pd.DataFrame, prefix: str, bundle: Mapping[str, object]) -> None:
    for q, values in bundle["quantiles"].items():
        frame[f"{prefix}_{quantile_name(float(q))}"] = np.asarray(values, dtype="float32")
    frame[f"{prefix}_prob_positive"] = np.asarray(
        bundle["probability_positive"], dtype="float32"
    )


def _bundle_from_frame(
    frame: pd.DataFrame,
    prefix: str,
    quantiles: Iterable[float],
) -> dict[str, object]:
    qs = tuple(float(q) for q in quantiles)
    return {
        "quantiles": {
            q: frame[f"{prefix}_{quantile_name(q)}"].to_numpy(float)
            for q in qs
        },
        "probability_positive": frame[f"{prefix}_prob_positive"].to_numpy(float),
    }


def _assign_regime(
    test_scale: np.ndarray,
    train_scale: np.ndarray,
    regime_quantiles: Iterable[float],
) -> tuple[np.ndarray, list[float]]:
    qs = tuple(float(q) for q in regime_quantiles)
    cut = np.quantile(np.asarray(train_scale, dtype=float), qs, method="linear")
    if not np.isfinite(cut).all() or not cut[0] <= cut[1]:
        raise RuntimeError("invalid train-only regime thresholds")
    scale = np.asarray(test_scale, dtype=float)
    labels = np.where(scale <= cut[0], "low", np.where(scale <= cut[1], "mid", "high"))
    return labels.astype(object), [float(cut[0]), float(cut[1])]


def _model_losses(
    frame: pd.DataFrame,
    prefix: str,
    quantiles: Iterable[float],
) -> np.ndarray:
    return mean_pinball_rows(
        frame["return_pct"].to_numpy(float),
        _bundle_from_frame(frame, prefix, quantiles)["quantiles"],
    )


def _daily_compare_from_losses(
    frame: pd.DataFrame,
    baseline_loss: np.ndarray,
    candidate_loss: np.ndarray,
) -> pd.DataFrame:
    return daily_loss_comparison(
        frame["origin_trading_day"], baseline_loss, candidate_loss
    )


def _bootstrap_suite(daily: pd.DataFrame, cfg: Mapping[str, Any]) -> dict[str, object]:
    return {
        str(block): moving_block_bootstrap_daily_loss(
            daily,
            block_length=int(block),
            reps=int(cfg["bootstrap_reps"]),
            seed=int(cfg["bootstrap_seed"]),
        )
        for block in cfg["moving_block_lengths_origin_days"]
    }


def _diagnostic_bootstrap(daily: pd.DataFrame, cfg: Mapping[str, Any]) -> dict[str, object]:
    block = int(cfg["diagnostic_block_length_origin_days"])
    return {
        str(block): moving_block_bootstrap_daily_loss(
            daily,
            block_length=block,
            reps=int(cfg["diagnostic_bootstrap_reps"]),
            seed=int(cfg["bootstrap_seed"]),
        )
    }


def _comparison_payload(
    frame: pd.DataFrame,
    baseline_prefix: str,
    candidate_prefix: str,
    quantiles: Iterable[float],
    cfg: Mapping[str, Any],
    *,
    diagnostic_bootstrap_only: bool = False,
) -> tuple[dict[str, object], pd.DataFrame]:
    base_loss = _model_losses(frame, baseline_prefix, quantiles)
    cand_loss = _model_losses(frame, candidate_prefix, quantiles)
    daily = _daily_compare_from_losses(frame, base_loss, cand_loss)
    payload = {
        "baseline": baseline_prefix,
        "candidate": candidate_prefix,
        "row_weighted_delta_pct": float(np.mean(base_loss - cand_loss)),
        "origin_day_equal_weight_delta_pct": float(
            daily["loss_delta_baseline_minus_candidate"].mean()
        ),
        "positive_delta_means_candidate_lower_loss": True,
        "moving_block_bootstrap": (
            _diagnostic_bootstrap(daily, cfg)
            if diagnostic_bootstrap_only
            else _bootstrap_suite(daily, cfg)
        ),
    }
    return payload, daily


def _assert_close(name: str, actual: float, expected: float, tolerance: float) -> None:
    if not np.isclose(float(actual), float(expected), rtol=0.0, atol=float(tolerance)):
        raise RuntimeError(
            f"V006 reproduction mismatch for {name}: actual={actual} expected={expected}"
        )


def verify_source_reproduction(
    horizon: int,
    oos: pd.DataFrame,
    fold_results: list[dict[str, object]],
    source_report_dir: Path,
    quantiles: Iterable[float],
    cfg: Mapping[str, Any],
) -> dict[str, object]:
    report_path = source_report_dir / f"h{int(horizon)}_benchmark.json"
    daily_path = source_report_dir / f"h{int(horizon)}_primary_daily_losses.csv"
    if not report_path.exists() or not daily_path.exists():
        raise FileNotFoundError(
            f"completed V006 artifacts required for V006.1: {report_path} / {daily_path}"
        )
    source = json.loads(report_path.read_text(encoding="utf-8"))
    if source.get("benchmark_version") != cfg["source_benchmark_version"]:
        raise RuntimeError("source V006 benchmark version mismatch")
    if source.get("model_version") != cfg["source_model_version"]:
        raise RuntimeError("source V006 model version mismatch")
    if source.get("dataset_contract") != cfg["dataset_contract"]:
        raise RuntimeError("source V006 dataset contract mismatch")
    if int(source.get("horizon_sessions")) != int(horizon):
        raise RuntimeError("source V006 horizon mismatch")
    tolerance = float(cfg["reproduction_absolute_tolerance"])
    if int(source["oos_rows"]) != int(len(oos)):
        raise RuntimeError("V006 OOS row count not reproduced")
    if int(source["oos_assets"]) != int(oos["asset_id"].nunique()):
        raise RuntimeError("V006 OOS asset count not reproduced")
    if int(source["oos_origin_days"]) != int(oos["origin_trading_day"].nunique()):
        raise RuntimeError("V006 OOS day count not reproduced")

    base_loss = _model_losses(oos, "train_empirical", quantiles)
    candidate_loss = _model_losses(oos, "volatility_scaled_empirical", quantiles)
    daily = _daily_compare_from_losses(oos, base_loss, candidate_loss)
    expected_daily = pd.read_csv(daily_path)
    if list(daily["origin_trading_day"].astype(str)) != list(
        expected_daily["origin_trading_day"].astype(str)
    ):
        raise RuntimeError("V006 primary daily-loss day sequence not reproduced")
    for column in (
        "rows",
        "baseline_loss",
        "candidate_loss",
        "loss_delta_baseline_minus_candidate",
    ):
        if column == "rows":
            if not np.array_equal(
                daily[column].to_numpy(int), expected_daily[column].to_numpy(int)
            ):
                raise RuntimeError("V006 primary daily-loss row counts not reproduced")
        else:
            if not np.allclose(
                daily[column].to_numpy(float),
                expected_daily[column].to_numpy(float),
                rtol=0.0,
                atol=tolerance,
            ):
                raise RuntimeError(f"V006 daily-loss column not reproduced: {column}")

    point = float(daily["loss_delta_baseline_minus_candidate"].mean())
    expected_point = float(source["primary_comparison"]["origin_day_equal_weight_delta_pct"])
    _assert_close("primary point", point, expected_point, tolerance)
    if len(fold_results) != len(source["fold_results"]):
        raise RuntimeError("V006 fold count not reproduced")
    for ours, prior in zip(fold_results, source["fold_results"]):
        if int(ours["fold_id"]) != int(prior["fold_id"]):
            raise RuntimeError("V006 fold identity not reproduced")
        if str(ours["first_test_day"]) != str(prior["first_test_day"]):
            raise RuntimeError("V006 first test day not reproduced")
        if str(ours["last_test_day"]) != str(prior["last_test_day"]):
            raise RuntimeError("V006 last test day not reproduced")
        _assert_close(
            f"fold {ours['fold_id']} primary",
            float(ours["primary_delta"]),
            float(prior["primary_comparison"]["origin_day_equal_weight_delta_pct"]),
            tolerance,
        )
    return {
        "status": "PASS",
        "source_report": str(report_path),
        "source_daily_losses": str(daily_path),
        "oos_rows": int(len(oos)),
        "oos_assets": int(oos["asset_id"].nunique()),
        "oos_origin_days": int(oos["origin_trading_day"].nunique()),
        "primary_delta_reproduced_pct": point,
        "absolute_tolerance": tolerance,
    }


def _tail_diagnostics(
    oos: pd.DataFrame,
    quantiles: Iterable[float],
    cfg: Mapping[str, Any],
) -> dict[str, object]:
    y = oos["return_pct"].to_numpy(float)
    out: dict[str, object] = {}
    for q in tuple(float(x) for x in quantiles):
        qn = quantile_name(q)
        base_pred = oos[f"train_empirical_{qn}"].to_numpy(float)
        candidate_pred = oos[f"volatility_scaled_empirical_{qn}"].to_numpy(float)
        asset_pred = oos[f"asset_empirical_{qn}"].to_numpy(float)
        base_loss = pinball_rows(y, base_pred, q)
        candidate_loss = pinball_rows(y, candidate_pred, q)
        asset_loss = pinball_rows(y, asset_pred, q)
        primary_daily = _daily_compare_from_losses(oos, base_loss, candidate_loss)
        asset_vs_candidate_daily = _daily_compare_from_losses(oos, asset_loss, candidate_loss)
        out[qn] = {
            "quantile": q,
            "candidate_vs_global": {
                "origin_day_equal_weight_delta_pct": float(
                    primary_daily["loss_delta_baseline_minus_candidate"].mean()
                ),
                "row_weighted_delta_pct": float(np.mean(base_loss - candidate_loss)),
                "moving_block_bootstrap": _diagnostic_bootstrap(primary_daily, cfg),
            },
            "candidate_vs_asset_reference": {
                "baseline": "asset_empirical",
                "candidate": "volatility_scaled_empirical",
                "origin_day_equal_weight_delta_pct": float(
                    asset_vs_candidate_daily["loss_delta_baseline_minus_candidate"].mean()
                ),
                "row_weighted_delta_pct": float(np.mean(asset_loss - candidate_loss)),
                "moving_block_bootstrap": _diagnostic_bootstrap(asset_vs_candidate_daily, cfg),
                "positive_delta_means_v006_candidate_lower_loss": True,
            },
            "candidate_empirical_cdf_at_prediction": float(np.mean(y <= candidate_pred)),
            "candidate_calibration_error": float(np.mean(y <= candidate_pred) - q),
            "global_calibration_error": float(np.mean(y <= base_pred) - q),
            "asset_calibration_error": float(np.mean(y <= asset_pred) - q),
        }
    return out


def _weighted_contributions(
    frame: pd.DataFrame,
    delta: np.ndarray,
    group_column: str,
) -> tuple[pd.DataFrame, dict[str, object]]:
    work = frame[["origin_trading_day", group_column]].copy()
    work["delta"] = np.asarray(delta, dtype=float)
    day_stats = work.groupby("origin_trading_day", sort=True)["delta"].agg(["sum", "count"])
    day_stats.columns = ["day_sum", "day_count"]
    work = work.join(day_stats, on="origin_trading_day")
    total_days = int(day_stats.shape[0])
    work["weighted_contribution"] = work["delta"] / work["day_count"] / float(total_days)
    grouped = work.groupby(group_column, sort=False).agg(
        rows=("delta", "size"),
        mean_row_delta_pct=("delta", "mean"),
        weighted_primary_contribution_pct=("weighted_contribution", "sum"),
    ).reset_index()
    grouped["abs_weighted_contribution_pct"] = grouped[
        "weighted_primary_contribution_pct"
    ].abs()
    abs_total = float(grouped["abs_weighted_contribution_pct"].sum())
    if abs_total > 0:
        grouped["abs_contribution_share"] = grouped["abs_weighted_contribution_pct"] / abs_total
    else:
        grouped["abs_contribution_share"] = 0.0
    grouped = grouped.sort_values("abs_weighted_contribution_pct", ascending=False).reset_index(drop=True)
    point = float((day_stats["day_sum"] / day_stats["day_count"]).mean())
    _assert_close(
        f"{group_column} additive contribution sum",
        float(grouped["weighted_primary_contribution_pct"].sum()),
        point,
        1e-10,
    )
    return grouped, {
        "primary_point_delta_pct": point,
        "groups": int(len(grouped)),
        "absolute_contribution_total_pct": abs_total,
    }


def _leave_one_group_out(
    frame: pd.DataFrame,
    delta: np.ndarray,
    group_column: str,
) -> pd.DataFrame:
    work = frame[["origin_trading_day", group_column]].copy()
    work["delta"] = np.asarray(delta, dtype=float)
    day = work.groupby("origin_trading_day", sort=True)["delta"].agg(["sum", "count"])
    day.columns = ["day_sum", "day_count"]
    full_point = float((day["day_sum"] / day["day_count"]).mean())
    total_days = int(len(day))
    grouped_day = work.groupby(["origin_trading_day", group_column], sort=False)["delta"].agg(["sum", "count"]).reset_index()
    grouped_day = grouped_day.join(day, on="origin_trading_day")
    remain_count = grouped_day["day_count"] - grouped_day["count"]
    if (remain_count <= 0).any():
        raise RuntimeError(f"cannot leave out entire {group_column} on an origin day")
    old_mean = grouped_day["day_sum"] / grouped_day["day_count"]
    new_mean = (grouped_day["day_sum"] - grouped_day["sum"]) / remain_count
    grouped_day["change"] = new_mean - old_mean
    change = grouped_day.groupby(group_column, sort=False)["change"].sum() / float(total_days)
    out = change.rename("change_from_full_pct").reset_index()
    out["leave_one_out_primary_delta_pct"] = full_point + out["change_from_full_pct"]
    return out.sort_values("leave_one_out_primary_delta_pct").reset_index(drop=True)


def _asset_sector_diagnostics(
    oos: pd.DataFrame,
    quantiles: Iterable[float],
    cfg: Mapping[str, Any],
) -> tuple[dict[str, object], pd.DataFrame, pd.DataFrame]:
    base_loss = _model_losses(oos, "train_empirical", quantiles)
    candidate_loss = _model_losses(oos, "volatility_scaled_empirical", quantiles)
    asset_ref_loss = _model_losses(oos, "asset_empirical", quantiles)
    delta = base_loss - candidate_loss
    candidate_vs_asset_delta = asset_ref_loss - candidate_loss

    asset_table, asset_meta = _weighted_contributions(oos, delta, "asset_id")
    asset_identity = oos[["asset_id", "ticker", "sector"]].drop_duplicates("asset_id")
    asset_table = asset_table.merge(asset_identity, on="asset_id", how="left", validate="one_to_one")
    per_asset_extra = pd.DataFrame({
        "asset_id": oos["asset_id"].to_numpy(int),
        "candidate_vs_asset_delta": candidate_vs_asset_delta,
        "scale": oos["asset_vol_20d_pct"].to_numpy(float),
    }).groupby("asset_id", sort=False).agg(
        candidate_vs_asset_reference_mean_row_delta_pct=("candidate_vs_asset_delta", "mean"),
        mean_asset_vol_20d_pct=("scale", "mean"),
    ).reset_index()
    asset_table = asset_table.merge(per_asset_extra, on="asset_id", how="left", validate="one_to_one")
    asset_loo = _leave_one_group_out(oos, delta, "asset_id")
    asset_table = asset_table.merge(asset_loo, on="asset_id", how="left", validate="one_to_one")
    asset_table = asset_table.sort_values("abs_weighted_contribution_pct", ascending=False).reset_index(drop=True)

    sector_table, sector_meta = _weighted_contributions(oos, delta, "sector")
    sector_extra = pd.DataFrame({
        "sector": oos["sector"].astype(str),
        "candidate_vs_asset_delta": candidate_vs_asset_delta,
        "scale": oos["asset_vol_20d_pct"].to_numpy(float),
    }).groupby("sector", sort=False).agg(
        candidate_vs_asset_reference_mean_row_delta_pct=("candidate_vs_asset_delta", "mean"),
        mean_asset_vol_20d_pct=("scale", "mean"),
    ).reset_index()
    sector_table = sector_table.merge(sector_extra, on="sector", how="left", validate="one_to_one")
    sector_loo = _leave_one_group_out(oos, delta, "sector")
    sector_table = sector_table.merge(sector_loo, on="sector", how="left", validate="one_to_one")
    sector_table = sector_table.sort_values("abs_weighted_contribution_pct", ascending=False).reset_index(drop=True)

    asset_top = {}
    for k in cfg["asset_concentration_top_k"]:
        kk = min(int(k), len(asset_table))
        asset_top[str(k)] = float(asset_table.head(kk)["abs_contribution_share"].sum())
    sector_top = {}
    for k in cfg["sector_concentration_top_k"]:
        kk = min(int(k), len(sector_table))
        sector_top[str(k)] = float(sector_table.head(kk)["abs_contribution_share"].sum())

    summary = {
        "asset": {
            **asset_meta,
            "positive_mean_row_delta_fraction": float(
                np.mean(asset_table["mean_row_delta_pct"].to_numpy(float) > 0.0)
            ),
            "top_k_absolute_contribution_share": asset_top,
            "leave_one_asset_out_min_primary_delta_pct": float(
                asset_table["leave_one_out_primary_delta_pct"].min()
            ),
            "leave_one_asset_out_max_primary_delta_pct": float(
                asset_table["leave_one_out_primary_delta_pct"].max()
            ),
            "worst_leave_one_out_asset": str(
                asset_table.loc[
                    asset_table["leave_one_out_primary_delta_pct"].idxmin(), "ticker"
                ]
            ),
        },
        "sector": {
            **sector_meta,
            "positive_mean_row_delta_fraction": float(
                np.mean(sector_table["mean_row_delta_pct"].to_numpy(float) > 0.0)
            ),
            "top_k_absolute_contribution_share": sector_top,
            "leave_one_sector_out_min_primary_delta_pct": float(
                sector_table["leave_one_out_primary_delta_pct"].min()
            ),
            "leave_one_sector_out_max_primary_delta_pct": float(
                sector_table["leave_one_out_primary_delta_pct"].max()
            ),
            "worst_leave_one_out_sector": str(
                sector_table.loc[
                    sector_table["leave_one_out_primary_delta_pct"].idxmin(), "sector"
                ]
            ),
        },
    }
    return summary, asset_table, sector_table


def _regime_diagnostics(
    oos: pd.DataFrame,
    quantiles: Iterable[float],
    cfg: Mapping[str, Any],
) -> tuple[dict[str, object], pd.DataFrame]:
    rows = []
    payload: dict[str, object] = {}
    for regime in ("low", "mid", "high"):
        subset = oos[oos["volatility_regime"] == regime].copy()
        if subset.empty:
            raise RuntimeError(f"empty volatility regime: {regime}")
        primary, _ = _comparison_payload(
            subset,
            "train_empirical",
            "volatility_scaled_empirical",
            quantiles,
            cfg,
            diagnostic_bootstrap_only=True,
        )
        candidate_vs_asset, _ = _comparison_payload(
            subset,
            "asset_empirical",
            "volatility_scaled_empirical",
            quantiles,
            cfg,
            diagnostic_bootstrap_only=True,
        )
        candidate_metrics = distribution_metrics(
            subset["return_pct"].to_numpy(float),
            _bundle_from_frame(subset, "volatility_scaled_empirical", quantiles)["quantiles"],
            _bundle_from_frame(subset, "volatility_scaled_empirical", quantiles)["probability_positive"],
        )
        item = {
            "regime": regime,
            "rows": int(len(subset)),
            "origin_days": int(subset["origin_trading_day"].nunique()),
            "mean_vol20_pct": float(subset["asset_vol_20d_pct"].mean()),
            "median_vol20_pct": float(subset["asset_vol_20d_pct"].median()),
            "candidate_vs_global": primary,
            "candidate_vs_asset_reference": candidate_vs_asset,
            "candidate_metrics": candidate_metrics,
        }
        payload[regime] = item
        rows.append({
            "regime": regime,
            "rows": item["rows"],
            "origin_days": item["origin_days"],
            "mean_vol20_pct": item["mean_vol20_pct"],
            "median_vol20_pct": item["median_vol20_pct"],
            "candidate_vs_global_daily_delta_pct": primary["origin_day_equal_weight_delta_pct"],
            "candidate_vs_asset_daily_delta_pct": candidate_vs_asset["origin_day_equal_weight_delta_pct"],
            "candidate_central50_coverage": candidate_metrics["central_50"]["coverage"],
            "candidate_central90_coverage": candidate_metrics["central_90"]["coverage"],
        })
    return payload, pd.DataFrame(rows)


def _calibration_drift(
    oos: pd.DataFrame,
    quantiles: Iterable[float],
    cfg: Mapping[str, Any],
) -> tuple[dict[str, object], pd.DataFrame]:
    block_size = int(cfg["calibration_block_origin_days"])
    records: list[dict[str, object]] = []
    for fold_id, fold_frame in oos.groupby("fold_id", sort=True):
        days = np.array(sorted(fold_frame["origin_trading_day"].astype(str).unique()))
        for block_index, start in enumerate(range(0, len(days), block_size)):
            block_days = days[start:start + block_size]
            subset = fold_frame[fold_frame["origin_trading_day"].isin(block_days)].copy()
            if subset.empty:
                continue
            row: dict[str, object] = {
                "fold_id": int(fold_id),
                "block_index_within_fold": int(block_index),
                "first_day": str(block_days[0]),
                "last_day": str(block_days[-1]),
                "origin_days": int(len(block_days)),
                "rows": int(len(subset)),
            }
            for prefix in PRIMARY_MODELS:
                bundle = _bundle_from_frame(subset, prefix, quantiles)
                metrics = distribution_metrics(
                    subset["return_pct"].to_numpy(float),
                    bundle["quantiles"],
                    bundle["probability_positive"],
                )
                row[f"{prefix}_mean_pinball_pct"] = metrics["mean_pinball_loss_pct"]
                row[f"{prefix}_central50_coverage"] = metrics["central_50"]["coverage"]
                row[f"{prefix}_central90_coverage"] = metrics["central_90"]["coverage"]
                for q in tuple(float(x) for x in quantiles):
                    qn = quantile_name(q)
                    row[f"{prefix}_{qn}_calibration_error"] = metrics["per_quantile"][qn]["calibration_error"]
            records.append(row)
    table = pd.DataFrame(records).sort_values(["fold_id", "block_index_within_fold"]).reset_index(drop=True)
    candidate = "volatility_scaled_empirical"
    summary = {
        "block_origin_days": block_size,
        "blocks": int(len(table)),
        "candidate_max_abs_central50_coverage_error": float(
            np.max(np.abs(table[f"{candidate}_central50_coverage"].to_numpy(float) - 0.50))
        ),
        "candidate_max_abs_central90_coverage_error": float(
            np.max(np.abs(table[f"{candidate}_central90_coverage"].to_numpy(float) - 0.90))
        ),
        "candidate_first_block_central50_coverage": float(table.iloc[0][f"{candidate}_central50_coverage"]),
        "candidate_last_block_central50_coverage": float(table.iloc[-1][f"{candidate}_central50_coverage"]),
        "candidate_first_block_central90_coverage": float(table.iloc[0][f"{candidate}_central90_coverage"]),
        "candidate_last_block_central90_coverage": float(table.iloc[-1][f"{candidate}_central90_coverage"]),
    }
    return summary, table


def _alternative_scale_diagnostics(
    oos: pd.DataFrame,
    quantiles: Iterable[float],
    cfg: Mapping[str, Any],
) -> dict[str, object]:
    out: dict[str, object] = {}
    for feature in cfg["alternative_scale_features"]:
        prefix = f"alt_{feature}"
        vs_global, _ = _comparison_payload(
            oos, "train_empirical", prefix, quantiles, cfg,
            diagnostic_bootstrap_only=True,
        )
        vs_v006, _ = _comparison_payload(
            oos, "volatility_scaled_empirical", prefix, quantiles, cfg,
            diagnostic_bootstrap_only=True,
        )
        bundle = _bundle_from_frame(oos, prefix, quantiles)
        metrics = distribution_metrics(
            oos["return_pct"].to_numpy(float),
            bundle["quantiles"],
            bundle["probability_positive"],
        )
        out[str(feature)] = {
            "model_prefix": prefix,
            "candidate_vs_global": vs_global,
            "alternative_vs_v006": vs_v006,
            "pooled_metrics": metrics,
            "claim_boundary": (
                "predeclared sensitivity only; cannot replace the completed V006 vol20 primary"
            ),
        }
    return out


def _fold_tail_table(
    oos: pd.DataFrame,
    quantiles: Iterable[float],
) -> pd.DataFrame:
    records = []
    for fold_id, subset in oos.groupby("fold_id", sort=True):
        y = subset["return_pct"].to_numpy(float)
        for q in tuple(float(x) for x in quantiles):
            qn = quantile_name(q)
            base = pinball_rows(y, subset[f"train_empirical_{qn}"].to_numpy(float), q)
            candidate = pinball_rows(
                y, subset[f"volatility_scaled_empirical_{qn}"].to_numpy(float), q
            )
            asset = pinball_rows(y, subset[f"asset_empirical_{qn}"].to_numpy(float), q)
            candidate_daily = _daily_compare_from_losses(subset, base, candidate)
            candidate_vs_asset_daily = _daily_compare_from_losses(subset, asset, candidate)
            records.append({
                "fold_id": int(fold_id),
                "quantile": q,
                "quantile_name": qn,
                "rows": int(len(subset)),
                "origin_days": int(subset["origin_trading_day"].nunique()),
                "candidate_vs_global_daily_delta_pct": float(
                    candidate_daily["loss_delta_baseline_minus_candidate"].mean()
                ),
                "candidate_vs_asset_daily_delta_pct": float(
                    candidate_vs_asset_daily["loss_delta_baseline_minus_candidate"].mean()
                ),
            })
    return pd.DataFrame(records)


def run_horizon_robustness(
    core_db: Path,
    horizon: int,
    cfg: Mapping[str, Any],
    source_report_dir: Path = DEFAULT_SOURCE_REPORT_DIR,
) -> tuple[dict[str, object], dict[str, pd.DataFrame]]:
    frame = load_horizon_frame(core_db, int(horizon), cfg)
    folds = build_purged_day_folds(
        frame,
        n_folds=int(cfg["outer_folds"]),
        initial_fraction=float(cfg["initial_fraction"]),
    )
    quantiles = tuple(float(q) for q in cfg["quantiles"])
    oos_parts: list[pd.DataFrame] = []
    fold_results: list[dict[str, object]] = []
    primary_scale = str(cfg["primary_scale_feature"])

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
            primary_scale,
            *[str(x) for x in cfg["alternative_scale_features"]],
        ]].copy()

        v006_train = train[["asset_id", "return_pct", primary_scale]].rename(
            columns={primary_scale: "scale_value"}
        )
        v006_test = test[["asset_id", primary_scale]].rename(
            columns={primary_scale: "scale_value"}
        )
        bundles = fit_predict_baselines(v006_train, v006_test, dict(cfg))
        for prefix in PRIMARY_MODELS:
            _store_bundle(test, prefix, bundles[prefix])

        base_loss_fold = mean_pinball_rows(
            test["return_pct"].to_numpy(float), bundles["train_empirical"]["quantiles"]
        )
        candidate_loss_fold = mean_pinball_rows(
            test["return_pct"].to_numpy(float), bundles["volatility_scaled_empirical"]["quantiles"]
        )
        fold_daily = _daily_compare_from_losses(test, base_loss_fold, candidate_loss_fold)

        regime_labels, regime_thresholds = _assign_regime(
            test[primary_scale].to_numpy(float),
            train[primary_scale].to_numpy(float),
            cfg["volatility_regime_train_quantiles"],
        )
        test["volatility_regime"] = regime_labels
        test["fold_id"] = int(fold.fold_id)

        alternative_fold: dict[str, object] = {}
        for feature in cfg["alternative_scale_features"]:
            feature = str(feature)
            alt_bundle = _scaled_empirical_bundle(
                train["return_pct"].to_numpy(float),
                train[feature].to_numpy(float),
                test[feature].to_numpy(float),
                quantiles,
            )
            prefix = f"alt_{feature}"
            _store_bundle(test, prefix, alt_bundle)
            alt_loss_fold = mean_pinball_rows(
                test["return_pct"].to_numpy(float), alt_bundle["quantiles"]
            )
            alt_daily = _daily_compare_from_losses(test, base_loss_fold, alt_loss_fold)
            direct_daily = _daily_compare_from_losses(test, candidate_loss_fold, alt_loss_fold)
            alternative_fold[feature] = {
                "vs_global_daily_delta_pct": float(
                    alt_daily["loss_delta_baseline_minus_candidate"].mean()
                ),
                "vs_v006_daily_delta_pct": float(
                    direct_daily["loss_delta_baseline_minus_candidate"].mean()
                ),
                "positive_vs_v006_means_alternative_lower_loss": True,
            }

        fold_results.append({
            "fold_id": int(fold.fold_id),
            "first_test_day": str(fold.first_test_day),
            "last_test_day": str(fold.last_test_day),
            "train_rows": int(len(train)),
            "test_rows": int(len(test)),
            "primary_delta": float(
                fold_daily["loss_delta_baseline_minus_candidate"].mean()
            ),
            "volatility_regime_train_thresholds_pct": regime_thresholds,
            "alternative_scale_diagnostics": alternative_fold,
        })
        oos_parts.append(test)

    oos = pd.concat(oos_parts, ignore_index=True)
    source_reproduction = verify_source_reproduction(
        int(horizon),
        oos,
        fold_results,
        source_report_dir,
        quantiles,
        cfg,
    )

    primary_comparison, primary_daily = _comparison_payload(
        oos,
        "train_empirical",
        "volatility_scaled_empirical",
        quantiles,
        cfg,
    )
    candidate_vs_asset, _ = _comparison_payload(
        oos,
        "asset_empirical",
        "volatility_scaled_empirical",
        quantiles,
        cfg,
    )
    tail = _tail_diagnostics(oos, quantiles, cfg)
    concentration, asset_table, sector_table = _asset_sector_diagnostics(oos, quantiles, cfg)
    regimes, regime_table = _regime_diagnostics(oos, quantiles, cfg)
    drift, drift_table = _calibration_drift(oos, quantiles, cfg)
    alternatives = _alternative_scale_diagnostics(oos, quantiles, cfg)
    fold_tail = _fold_tail_table(oos, quantiles)

    candidate_bundle = _bundle_from_frame(oos, "volatility_scaled_empirical", quantiles)
    global_bundle = _bundle_from_frame(oos, "train_empirical", quantiles)
    asset_bundle = _bundle_from_frame(oos, "asset_empirical", quantiles)
    pooled_metrics = {
        "train_empirical": distribution_metrics(
            oos["return_pct"].to_numpy(float),
            global_bundle["quantiles"],
            global_bundle["probability_positive"],
        ),
        "volatility_scaled_empirical": distribution_metrics(
            oos["return_pct"].to_numpy(float),
            candidate_bundle["quantiles"],
            candidate_bundle["probability_positive"],
        ),
        "asset_empirical": distribution_metrics(
            oos["return_pct"].to_numpy(float),
            asset_bundle["quantiles"],
            asset_bundle["probability_positive"],
        ),
    }

    report = {
        "robustness_version": cfg["version"],
        "source_benchmark_version": cfg["source_benchmark_version"],
        "source_model_version": cfg["source_model_version"],
        "dataset_contract": cfg["dataset_contract"],
        "market_feature_version": cfg["market_feature_version"],
        "label_version": cfg["label_version"],
        "horizon_sessions": int(horizon),
        "target": cfg["target"],
        "oos_rows": int(len(oos)),
        "oos_assets": int(oos["asset_id"].nunique()),
        "oos_origin_days": int(oos["origin_trading_day"].nunique()),
        "fold_contract": fold_summary(folds),
        "fold_diagnostics": fold_results,
        "source_reproduction": source_reproduction,
        "completed_v006_primary_reanalysis": primary_comparison,
        "secondary_candidate_vs_asset_reference": candidate_vs_asset,
        "pooled_metrics": pooled_metrics,
        "tail_specific": tail,
        "concentration": concentration,
        "volatility_regimes": regimes,
        "calibration_drift": drift,
        "alternative_causal_scales": alternatives,
        "scientific_contract": {
            "completed_v006_primary_unchanged": True,
            "diagnostics_can_narrow_or_falsify_claim": True,
            "alternative_scales_cannot_retroactively_replace_vol20": True,
            "no_new_model_training": True,
            "no_posthoc_candidate_selection": True,
            "all_horizons_required": True,
            "test_outcomes_used_for_prediction": False,
            "strict_historical_pit": False,
            "current_cohort_not_survivorship_free": True,
            "terminal_return_not_joint_path": True,
            "production_ready": False,
        },
    }
    tables = {
        "primary_daily_losses": primary_daily,
        "asset_concentration": asset_table,
        "sector_concentration": sector_table,
        "regime_summary": regime_table,
        "calibration_drift": drift_table,
        "fold_tail": fold_tail,
    }
    return report, tables
