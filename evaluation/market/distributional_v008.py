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
    quantile_name,
)
from models.market.distributional_v008_conditional_quantiles import (
    apply_standardized_calibration,
    asset_empirical_bundle,
    baseline_vol63_bundle,
    calibration_shifts,
    calibrate_probability_isotonic,
    constant_standardized_predictions,
    crossing_fraction,
    empirical_bundle,
    fit_probability_model,
    fit_quantile_models,
    load_horizon,
    monotone_rearrange,
    raw_model_standardized_predictions,
    reconstruct_return_bundle,
    split_recent_days,
)

MODEL_NAMES = (
    "hgb_full_endogenous_calibrated",
    "hgb_own_state_calibrated",
    "hgb_scale_only_calibrated",
    "vol63_recent_calibrated",
    "vol63_raw",
    "vol20_raw",
    "asset_empirical",
    "train_empirical",
)


def _json_sha(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


def _day_equal_pinball(frame: pd.DataFrame, y: np.ndarray, bundle: Mapping[str, Any]) -> float:
    losses = mean_pinball_rows(y, bundle["quantiles"])
    daily = pd.DataFrame({"day": frame["origin_trading_day"].astype(str), "loss": losses}).groupby("day", sort=True)["loss"].mean()
    return float(daily.mean())


def select_profile(
    development: pd.DataFrame,
    features: list[str],
    cfg: Mapping[str, Any],
) -> tuple[str, pd.DataFrame, dict[str, Any]]:
    inner = split_recent_days(
        development,
        validation_fraction=float(cfg["inner_validation_fraction"]),
        minimum_train_days=int(cfg["minimum_inner_train_origin_days"]),
        minimum_validation_days=int(cfg["minimum_inner_validation_origin_days"]),
    )
    rows = []
    quantiles = tuple(float(q) for q in cfg["quantiles"])
    for name, profile in cfg["model_profiles"].items():
        anchor, models = fit_quantile_models(inner.train, features, cfg["residual_scale_feature"], quantiles, profile)
        zpred, usable = raw_model_standardized_predictions(models, inner.validation, features, cfg["residual_scale_feature"])
        pre_cross = crossing_fraction(zpred, usable)
        zpred = monotone_rearrange(zpred, usable)
        bundle = reconstruct_return_bundle(zpred, usable, anchor, inner.validation, cfg["residual_scale_feature"])
        score = _day_equal_pinball(inner.validation, inner.validation["return_pct"].to_numpy(float), bundle)
        rows.append({
            "profile": name,
            "inner_origin_day_equal_pinball_loss_pct": score,
            "raw_quantile_crossing_fraction": pre_cross,
        })
    table = pd.DataFrame(rows).sort_values(["inner_origin_day_equal_pinball_loss_pct", "profile"], kind="mergesort").reset_index(drop=True)
    selected = str(table.iloc[0]["profile"])
    meta = {
        "selected_profile": selected,
        "first_validation_day": inner.first_validation_day,
        "inner_train_rows": int(len(inner.train)),
        "inner_validation_rows": int(len(inner.validation)),
        "inner_train_origin_days": int(inner.train["origin_trading_day"].nunique()),
        "inner_validation_origin_days": int(inner.validation["origin_trading_day"].nunique()),
        "selection_rule": "minimum inner origin-day-equal mean pinball across all five quantiles; profile only, not feature-family selection",
    }
    return selected, table, meta


def _fit_calibrated_model_bundle(
    development: pd.DataFrame,
    calibration: pd.DataFrame,
    test: pd.DataFrame,
    features: list[str],
    profile: Mapping[str, Any],
    cfg: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    qs = tuple(float(q) for q in cfg["quantiles"])
    anchor, models = fit_quantile_models(development, features, cfg["residual_scale_feature"], qs, profile)
    cal_raw, cal_usable = raw_model_standardized_predictions(models, calibration, features, cfg["residual_scale_feature"])
    test_raw, test_usable = raw_model_standardized_predictions(models, test, features, cfg["residual_scale_feature"])
    cal_cross = crossing_fraction(cal_raw, cal_usable)
    test_cross = crossing_fraction(test_raw, test_usable)
    cal_raw = monotone_rearrange(cal_raw, cal_usable)
    test_raw = monotone_rearrange(test_raw, test_usable)
    shifts = calibration_shifts(calibration, cal_raw, cal_usable, anchor, cfg["residual_scale_feature"])
    test_cal = apply_standardized_calibration(test_raw, test_usable, shifts)
    bundle = reconstruct_return_bundle(test_cal, test_usable, anchor, test, cfg["residual_scale_feature"])
    classifier = fit_probability_model(development, features, profile)
    raw_cal_p = classifier.predict_proba(calibration[features].to_numpy(float))[:, 1]
    raw_test_p = classifier.predict_proba(test[features].to_numpy(float))[:, 1]
    calibrated_p, probability_diag = calibrate_probability_isotonic(raw_cal_p, calibration, raw_test_p)
    bundle["probability_positive"] = calibrated_p
    return bundle, {
        "calibration_shifts_standardized": {quantile_name(q): float(v) for q, v in shifts.items()},
        "raw_crossing_fraction_calibration": cal_cross,
        "raw_crossing_fraction_test": test_cross,
        "positive_scale_calibration_rows": int(cal_usable.sum()),
        "positive_scale_test_rows": int(test_usable.sum()),
        "probability_calibration": probability_diag,
    }


def _fit_calibrated_vol63(
    development: pd.DataFrame,
    calibration: pd.DataFrame,
    test: pd.DataFrame,
    cfg: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    qs = tuple(float(q) for q in cfg["quantiles"])
    from models.market.distributional_v008_conditional_quantiles import fit_residual_anchor
    anchor = fit_residual_anchor(development, cfg["residual_scale_feature"], qs)
    cal_z, cal_usable = constant_standardized_predictions(anchor, calibration, cfg["residual_scale_feature"])
    test_z, test_usable = constant_standardized_predictions(anchor, test, cfg["residual_scale_feature"])
    shifts = calibration_shifts(calibration, cal_z, cal_usable, anchor, cfg["residual_scale_feature"])
    test_z = apply_standardized_calibration(test_z, test_usable, shifts)
    bundle = reconstruct_return_bundle(test_z, test_usable, anchor, test, cfg["residual_scale_feature"])
    raw_cal_bundle = baseline_vol63_bundle(anchor, calibration, cfg["residual_scale_feature"], qs)
    raw_test_bundle = baseline_vol63_bundle(anchor, test, cfg["residual_scale_feature"], qs)
    calibrated_p, probability_diag = calibrate_probability_isotonic(
        raw_cal_bundle["probability_positive"], calibration, raw_test_bundle["probability_positive"]
    )
    bundle["probability_positive"] = calibrated_p
    return bundle, {
        "calibration_shifts_standardized": {quantile_name(q): float(v) for q, v in shifts.items()},
        "probability_calibration": probability_diag,
    }


def _store_bundle(frame: pd.DataFrame, prefix: str, bundle: Mapping[str, Any]) -> None:
    for q, values in bundle["quantiles"].items():
        frame[f"{prefix}_{quantile_name(float(q))}"] = np.asarray(values, dtype="float32")
    frame[f"{prefix}_prob_positive"] = np.asarray(bundle["probability_positive"], dtype="float32")


def _bundle_from_columns(frame: pd.DataFrame, prefix: str, quantiles: tuple[float, ...]) -> dict[str, Any]:
    return {
        "quantiles": {q: frame[f"{prefix}_{quantile_name(q)}"].to_numpy(float) for q in quantiles},
        "probability_positive": frame[f"{prefix}_prob_positive"].to_numpy(float),
    }


def _comparison(oos: pd.DataFrame, actual: np.ndarray, baseline: Mapping[str, Any], candidate: Mapping[str, Any], cfg: Mapping[str, Any]) -> tuple[dict[str, Any], pd.DataFrame]:
    base_loss = mean_pinball_rows(actual, baseline["quantiles"])
    cand_loss = mean_pinball_rows(actual, candidate["quantiles"])
    daily = daily_loss_comparison(oos["origin_trading_day"], base_loss, cand_loss)
    boot = {}
    for block in cfg["moving_block_lengths_origin_days"]:
        boot[str(block)] = moving_block_bootstrap_daily_loss(
            daily,
            block_length=int(block),
            reps=int(cfg["bootstrap_reps"]),
            seed=int(cfg["bootstrap_seed"]),
        )
    return {
        "origin_day_equal_weight_delta_pct": float(daily["loss_delta_baseline_minus_candidate"].mean()),
        "row_weighted_delta_pct": float(np.mean(base_loss - cand_loss)),
        "positive_delta_means_candidate_lower_loss": True,
        "moving_block_bootstrap": boot,
    }, daily


def mean_absolute_quantile_calibration_error(metrics: Mapping[str, Any]) -> float:
    return float(np.mean([abs(float(v["calibration_error"])) for v in metrics["per_quantile"].values()]))


def _gate(primary: Mapping[str, Any], candidate_metrics: Mapping[str, Any], ref_metrics: Mapping[str, Any]) -> dict[str, Any]:
    ci = primary["moving_block_bootstrap"]["10"]["ci95"]
    point = float(primary["origin_day_equal_weight_delta_pct"])
    cand_cal = mean_absolute_quantile_calibration_error(candidate_metrics)
    ref_cal = mean_absolute_quantile_calibration_error(ref_metrics)
    if float(ci[0]) > 0.0 and cand_cal <= ref_cal:
        status = "PASS_STRONG"
    elif float(ci[0]) > 0.0:
        status = "PASS_SCORE_ONLY_CALIBRATION_WORSE"
    elif float(ci[1]) < 0.0:
        status = "FAIL_SIGNIFICANT"
    elif point <= 0.0:
        status = "FAIL"
    else:
        status = "INCONCLUSIVE_POSITIVE_POINT"
    return {
        "status": status,
        "primary_point_delta_pct": point,
        "primary_block10_ci95": [float(ci[0]), float(ci[1])],
        "candidate_mean_abs_quantile_calibration_error": cand_cal,
        "reference_mean_abs_quantile_calibration_error": ref_cal,
        "calibration_not_worse": bool(cand_cal <= ref_cal),
    }


def run_horizon(core_db: Path, horizon: int, cfg: Mapping[str, Any], manifest: Mapping[str, Any]) -> tuple[dict[str, Any], pd.DataFrame, dict[str, pd.DataFrame]]:
    frame = load_horizon(core_db, int(horizon), cfg, manifest)
    folds = build_purged_day_folds(frame, n_folds=int(cfg["outer_folds"]), initial_fraction=float(cfg["initial_fraction"]))
    qs = tuple(float(q) for q in cfg["quantiles"])
    fold_results = []
    oos_parts = []
    selection_tables: dict[str, pd.DataFrame] = {}

    full_features = list(manifest["full_endogenous"])
    own_features = list(manifest["own_state"])
    scale_features = list(manifest["scale_only"])

    for fold in folds:
        outer_train = frame.loc[list(fold.train_index)].copy()
        test_cols = ["state_id", "asset_id", "ticker", "sector", "origin_trading_day", "target_trading_day", "return_pct", *full_features]
        test = frame.loc[list(fold.test_index), list(dict.fromkeys(test_cols))].copy()
        cal_split = split_recent_days(
            outer_train,
            validation_days=int(cfg["recent_calibration_origin_days"]),
            minimum_train_days=int(cfg["minimum_inner_train_origin_days"]) + int(cfg["minimum_inner_validation_origin_days"]),
            minimum_validation_days=int(cfg["recent_calibration_origin_days"]),
        )
        development, calibration = cal_split.train, cal_split.validation
        profile_name, selection_table, selection_meta = select_profile(development, full_features, cfg)
        profile = cfg["model_profiles"][profile_name]
        selection_tables[f"fold{fold.fold_id}_profile_selection"] = selection_table

        full_bundle, full_diag = _fit_calibrated_model_bundle(development, calibration, test, full_features, profile, cfg)
        own_bundle, own_diag = _fit_calibrated_model_bundle(development, calibration, test, own_features, profile, cfg)
        scale_bundle, scale_diag = _fit_calibrated_model_bundle(development, calibration, test, scale_features, profile, cfg)
        vol63_cal_bundle, vol63_cal_diag = _fit_calibrated_vol63(development, calibration, test, cfg)

        # Historical controls are fit on the complete outer train and are never used for profile selection.
        from models.market.distributional_v008_conditional_quantiles import fit_residual_anchor
        vol63_anchor = fit_residual_anchor(outer_train, "asset_vol_63d_pct", qs)
        vol20_anchor = fit_residual_anchor(outer_train, "asset_vol_20d_pct", qs)
        vol63_raw = baseline_vol63_bundle(vol63_anchor, test, "asset_vol_63d_pct", qs)
        vol20_raw = baseline_vol63_bundle(vol20_anchor, test, "asset_vol_20d_pct", qs)
        train_emp = empirical_bundle(outer_train, test, qs)
        asset_emp = asset_empirical_bundle(outer_train, test, qs)

        bundles = {
            "hgb_full_endogenous_calibrated": full_bundle,
            "hgb_own_state_calibrated": own_bundle,
            "hgb_scale_only_calibrated": scale_bundle,
            "vol63_recent_calibrated": vol63_cal_bundle,
            "vol63_raw": vol63_raw,
            "vol20_raw": vol20_raw,
            "asset_empirical": asset_emp,
            "train_empirical": train_emp,
        }
        metrics = {name: distribution_metrics(test["return_pct"].to_numpy(float), b["quantiles"], b["probability_positive"]) for name, b in bundles.items()}
        for name, bundle in bundles.items():
            _store_bundle(test, name, bundle)
        test["fold_id"] = int(fold.fold_id)
        oos_parts.append(test)
        primary, _ = _comparison(test, test["return_pct"].to_numpy(float), bundles[cfg["primary_reference"]], bundles[cfg["primary_candidate"]], cfg)
        fold_results.append({
            "fold_id": int(fold.fold_id),
            "first_test_day": fold.first_test_day,
            "last_test_day": fold.last_test_day,
            "outer_train_rows": int(len(outer_train)),
            "development_rows": int(len(development)),
            "calibration_rows": int(len(calibration)),
            "test_rows": int(len(test)),
            "first_calibration_day": cal_split.first_validation_day,
            "profile_selection": selection_meta,
            "selected_profile": profile_name,
            "candidate_diagnostics": full_diag,
            "own_control_diagnostics": own_diag,
            "scale_control_diagnostics": scale_diag,
            "reference_calibration_diagnostics": vol63_cal_diag,
            "metrics": metrics,
            "primary_comparison": {
                "origin_day_equal_weight_delta_pct": primary["origin_day_equal_weight_delta_pct"],
                "row_weighted_delta_pct": primary["row_weighted_delta_pct"],
                "positive_delta_means_candidate_lower_loss": True,
            },
        })

    oos = pd.concat(oos_parts, ignore_index=True)
    actual = oos["return_pct"].to_numpy(float)
    bundles = {name: _bundle_from_columns(oos, name, qs) for name in MODEL_NAMES}
    pooled_metrics = {name: distribution_metrics(actual, b["quantiles"], b["probability_positive"]) for name, b in bundles.items()}
    primary, primary_daily = _comparison(oos, actual, bundles[cfg["primary_reference"]], bundles[cfg["primary_candidate"]], cfg)
    comparisons: dict[str, Any] = {}
    tables: dict[str, pd.DataFrame] = {"primary_daily_losses": primary_daily}
    for reference in cfg["secondary_references"]:
        comp, daily = _comparison(oos, actual, bundles[reference], bundles[cfg["primary_candidate"]], cfg)
        comparisons[reference] = comp
        tables[f"vs_{reference}_daily_losses"] = daily
    diagnostics = {}
    for candidate_name in cfg["diagnostic_candidates"]:
        comp_ref, daily_ref = _comparison(oos, actual, bundles[cfg["primary_reference"]], bundles[candidate_name], cfg)
        comp_full, daily_full = _comparison(oos, actual, bundles[candidate_name], bundles[cfg["primary_candidate"]], cfg)
        diagnostics[candidate_name] = {
            "vs_primary_reference": comp_ref,
            "full_candidate_vs_this_control": comp_full,
        }
        tables[f"{candidate_name}_vs_primary_daily_losses"] = daily_ref
        tables[f"full_vs_{candidate_name}_daily_losses"] = daily_full
    recalibration_gain, recal_daily = _comparison(oos, actual, bundles["vol63_raw"], bundles["vol63_recent_calibrated"], cfg)
    tables["vol63_recalibration_daily_losses"] = recal_daily
    gate = _gate(primary, pooled_metrics[cfg["primary_candidate"]], pooled_metrics[cfg["primary_reference"]])

    report = {
        "benchmark_version": cfg["version"],
        "model_version": cfg["model_version"],
        "dataset_contract": cfg["dataset_contract"],
        "market_feature_version": cfg["market_feature_version"],
        "label_version": cfg["label_version"],
        "horizon_sessions": int(horizon),
        "oos_rows": int(len(oos)),
        "oos_assets": int(oos["asset_id"].nunique()),
        "oos_origin_days": int(oos["origin_trading_day"].nunique()),
        "oos_first_day": str(oos["origin_trading_day"].min()),
        "oos_last_day": str(oos["origin_trading_day"].max()),
        "feature_manifest_sha256": _json_sha(manifest),
        "feature_manifest_counts": manifest["counts"],
        "primary_reference": cfg["primary_reference"],
        "primary_candidate": cfg["primary_candidate"],
        "fold_contract": fold_summary(folds),
        "fold_results": fold_results,
        "pooled_metrics": pooled_metrics,
        "primary_comparison": primary,
        "secondary_comparisons": comparisons,
        "information_controls": diagnostics,
        "vol63_recent_recalibration_vs_raw": recalibration_gain,
        "horizon_gate": gate,
        "interpretation_contract": {
            "full_beats_reference_and_scale_control": "evidence of endogenous non-scale conditional information",
            "scale_only_beats_reference": "scale state contains nonlinear information not captured by empirical vol63; does not validate V007 form",
            "full_fails_reference": "current endogenous X_t has not earned incremental distributional information beyond calibrated vol63; next step should enrich information before increasing capacity",
            "no_feature_family_may_rescue_failed_primary_posthoc": True,
        },
    }
    tables.update(selection_tables)
    return report, oos, tables
