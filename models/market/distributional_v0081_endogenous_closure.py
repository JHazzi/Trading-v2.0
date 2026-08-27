from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from models.market.distributional_v008_conditional_quantiles import (
    REQUIRED_QUANTILES,
    baseline_vol63_bundle,
    crossing_fraction,
    fit_probability_model,
    fit_quantile_models,
    fit_residual_anchor,
    monotone_rearrange,
    raw_model_standardized_predictions,
    reconstruct_return_bundle,
    resolve_feature_manifest,
    validate_feature_manifest,
)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CORE_DB = ROOT / "data" / "processed" / "market_daily_v003_core.db"
DEFAULT_CONFIG = ROOT / "config" / "market_brain_distributional_v0081.json"


def load_config(path: Path = DEFAULT_CONFIG) -> dict[str, Any]:
    cfg = json.loads(path.read_text(encoding="utf-8"))
    if cfg["version"] != "market_brain_distributional_v0081_endogenous_closure_v001":
        raise ValueError("unexpected V008.1 version")
    if cfg["model_version"] != "market_brain_distributional_v0081_hgb_own_state_raw_v001":
        raise ValueError("unexpected V008.1 model version")
    if cfg["source_v008_version"] != "market_brain_distributional_v008_conditional_residual_quantiles_v0011":
        raise ValueError("unexpected V008 source version")
    if cfg["market_feature_version"] != "market_daily_state_v003_core":
        raise ValueError("market feature version changed")
    if cfg["label_version"] != "market_daily_reaction_v003_core":
        raise ValueError("label version changed")
    if cfg["target"] != "return_pct":
        raise ValueError("V008.1 target changed")
    if tuple(float(x) for x in cfg["quantiles"]) != REQUIRED_QUANTILES:
        raise ValueError("V008.1 quantiles changed")
    if cfg["horizons_sessions"] != [1, 3, 5, 10] or int(cfg["primary_horizon_sessions"]) != 1:
        raise ValueError("V008.1 horizon contract changed")
    if cfg["primary_reference"] != "vol63_raw" or cfg["primary_candidate"] != "hgb_own_state_raw":
        raise ValueError("V008.1 primary comparison changed")
    if cfg["residual_scale_feature"] != "asset_vol_63d_pct":
        raise ValueError("V008.1 residual scale changed")
    if cfg["post_model_quantile_calibration"] != "none" or cfg["probability_calibration"] != "none":
        raise ValueError("V008.1 forbids recent post-model recalibration")
    if len(cfg["frozen_own_features"]) != 14 or len(set(cfg["frozen_own_features"])) != 14:
        raise ValueError("V008.1 frozen own-state family changed")
    placebo = cfg["capacity_placebo"]
    if placebo["enabled_horizons_sessions"] != [1] or len(set(placebo["seeds"])) < 5:
        raise ValueError("V008.1 capacity-placebo contract changed")
    if not set(placebo["preserved_aligned_features"]).issubset(cfg["frozen_own_features"]):
        raise ValueError("placebo preserved features are outside own state")
    for key in (
        "external_proxy_features_added",
        "event_features_added",
        "graph_features_added",
        "macro_features_added",
        "broker_cost_used_for_training",
    ):
        if cfg.get(key) is not False:
            raise ValueError(f"deferred information enabled: {key}")
    for key in (
        "historical_sample_reused_after_v008",
        "fresh_untouched_holdout_required_for_promotion",
        "no_hyperparameter_selection",
        "no_posthoc_horizon_rescue",
        "no_posthoc_feature_change",
    ):
        if cfg.get(key) is not True:
            raise ValueError(f"scientific guard disabled: {key}")
    if cfg.get("strict_historical_pit") is not False:
        raise ValueError("Core V003 is not strict PIT")
    return cfg


def validate_frozen_manifest(manifest: Mapping[str, Any], cfg: Mapping[str, Any]) -> None:
    validate_feature_manifest(manifest, cfg)
    resolved = list(manifest["own_state"])
    expected = list(cfg["frozen_own_features"])
    if resolved != expected:
        raise RuntimeError(
            "resolved own-state manifest differs from the frozen V008 family; "
            f"expected={expected}, resolved={resolved}"
        )
    preserved = list(cfg["capacity_placebo"]["preserved_aligned_features"])
    if preserved != list(manifest["scale_only"]):
        raise RuntimeError("capacity placebo must preserve exactly the frozen scale-only family")


def fit_raw_model_bundle(
    train: pd.DataFrame,
    test: pd.DataFrame,
    features: list[str],
    cfg: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Fit the frozen quantile/classification heads with no post-fit calibration."""
    quantiles = tuple(float(q) for q in cfg["quantiles"])
    profile = cfg["fixed_model_profile"]
    anchor, models = fit_quantile_models(
        train,
        features,
        str(cfg["residual_scale_feature"]),
        quantiles,
        profile,
    )
    raw, usable = raw_model_standardized_predictions(
        models,
        test,
        features,
        str(cfg["residual_scale_feature"]),
    )
    pre_crossing = crossing_fraction(raw, usable)
    rearranged = monotone_rearrange(raw, usable)
    bundle = reconstruct_return_bundle(
        rearranged,
        usable,
        anchor,
        test,
        str(cfg["residual_scale_feature"]),
    )
    classifier = fit_probability_model(train, features, profile)
    probability = classifier.predict_proba(test[features].to_numpy(float))[:, 1]
    bundle["probability_positive"] = np.clip(probability, 0.0, 1.0)
    return bundle, {
        "raw_quantile_crossing_fraction": pre_crossing,
        "positive_scale_test_rows": int(usable.sum()),
        "post_model_quantile_calibration": "none",
        "probability_calibration": "none",
    }


def fit_raw_vol63_bundle(
    train: pd.DataFrame,
    test: pd.DataFrame,
    cfg: Mapping[str, Any],
) -> dict[str, Any]:
    quantiles = tuple(float(q) for q in cfg["quantiles"])
    anchor = fit_residual_anchor(train, str(cfg["residual_scale_feature"]), quantiles)
    return baseline_vol63_bundle(
        anchor,
        test,
        str(cfg["residual_scale_feature"]),
        quantiles,
    )


def make_capacity_placebo_train(
    train: pd.DataFrame,
    own_features: list[str],
    preserved_features: list[str],
    seed: int,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Destroy asset alignment but preserve each day's joint state support."""
    if not own_features or not set(preserved_features).issubset(own_features):
        raise ValueError("invalid placebo feature families")
    preserved_set = set(preserved_features)
    permuted_features = [f for f in own_features if f not in preserved_set]
    if not permuted_features:
        raise ValueError("placebo has no incremental features to permute")
    out = train.copy()
    rng = np.random.default_rng(int(seed))
    column_positions = [out.columns.get_loc(name) for name in permuted_features]
    singleton_days = 0
    changed_rows = 0

    groups = train.groupby("origin_trading_day", sort=False).indices
    for positions_raw in groups.values():
        positions = np.asarray(positions_raw, dtype=int)
        if len(positions) < 2:
            singleton_days += 1
            continue
        order = rng.permutation(len(positions))
        destinations = positions[order]
        sources = positions[np.roll(order, 1)]
        source_values = train.iloc[sources][permuted_features].to_numpy(copy=True)
        out.iloc[destinations, column_positions] = source_values
        changed_rows += len(positions)

    if changed_rows == 0:
        raise RuntimeError("capacity placebo has no multi-asset origin days")
    for name in preserved_features:
        if not out[name].equals(train[name]):
            raise RuntimeError(f"placebo changed preserved scale feature {name}")
    for name in ("return_pct", "asset_id", "origin_trading_day", "target_trading_day"):
        if name in train and not out[name].equals(train[name]):
            raise RuntimeError(f"placebo changed protected column {name}")

    return out, {
        "seed": int(seed),
        "permutation_unit": "within_origin_day_across_assets",
        "policy": "joint_derangement_of_non_scale_own_features",
        "permuted_features": permuted_features,
        "preserved_features": list(preserved_features),
        "origin_days": int(len(groups)),
        "singleton_origin_days": int(singleton_days),
        "deranged_rows": int(changed_rows),
        "deranged_row_fraction": float(changed_rows / len(train)),
    }
