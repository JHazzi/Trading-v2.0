from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.isotonic import IsotonicRegression

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CORE_DB = ROOT / "data" / "processed" / "market_daily_v003_core.db"
DEFAULT_CONFIG = ROOT / "config" / "market_brain_distributional_v008.json"
REQUIRED_QUANTILES = (0.05, 0.25, 0.5, 0.75, 0.95)


@dataclass(frozen=True)
class SplitBundle:
    train: pd.DataFrame
    validation: pd.DataFrame
    first_validation_day: str


@dataclass(frozen=True)
class ResidualAnchor:
    location: float
    standardized_sorted: np.ndarray
    standardized_quantiles: dict[float, float]
    global_return_quantiles: dict[float, float]
    positive_probability: float


def load_config(path: Path = DEFAULT_CONFIG) -> dict[str, Any]:
    cfg = json.loads(path.read_text(encoding="utf-8"))
    if cfg["version"] != "market_brain_distributional_v008_conditional_residual_quantiles_v001":
        raise ValueError("unexpected V008 version")
    if cfg["model_version"] != "market_brain_distributional_v008_hgb_residual_quantiles_v001":
        raise ValueError("unexpected V008 model version")
    if cfg["market_feature_version"] != "market_daily_state_v003_core":
        raise ValueError("market feature version changed")
    if cfg["label_version"] != "market_daily_reaction_v003_core":
        raise ValueError("label version changed")
    if cfg["target"] != "return_pct":
        raise ValueError("V008 target changed")
    if tuple(float(x) for x in cfg["quantiles"]) != REQUIRED_QUANTILES:
        raise ValueError("V008 quantiles changed")
    if cfg["primary_reference"] != "vol63_recent_calibrated":
        raise ValueError("V008 primary reference changed")
    if cfg["primary_candidate"] != "hgb_full_endogenous_calibrated":
        raise ValueError("V008 primary candidate changed")
    if cfg["residual_scale_feature"] != "asset_vol_63d_pct":
        raise ValueError("V008 residual scale changed")
    if cfg["recent_calibration_origin_days"] < 63:
        raise ValueError("calibration window too short")
    if not 0.0 < float(cfg["inner_validation_fraction"]) < 0.5:
        raise ValueError("invalid inner validation fraction")
    if set(cfg["model_profiles"]) != {"shallow_regularized", "medium_regularized"}:
        raise ValueError("V008 model profile set changed")
    for key in (
        "external_proxy_features_added",
        "event_features_added",
        "graph_features_added",
        "macro_features_added",
        "broker_cost_used_for_training",
    ):
        if cfg.get(key) is not False:
            raise ValueError(f"deferred information enabled: {key}")
    for key in ("no_best_horizon_selection", "no_posthoc_feature_family_rescue"):
        if cfg.get(key) is not True:
            raise ValueError(f"scientific guard disabled: {key}")
    if cfg.get("strict_historical_pit") is not False:
        raise ValueError("Core V003 is not strict PIT")
    return cfg


def _sqlite_numeric_type(declared: str) -> bool:
    value = (declared or "").upper()
    return any(token in value for token in ("INT", "REAL", "FLOA", "DOUB", "NUM"))


def resolve_feature_manifest(core_db: Path, cfg: Mapping[str, Any]) -> dict[str, Any]:
    rules = cfg["feature_resolution"]
    with sqlite3.connect(core_db) as conn:
        rows = conn.execute("PRAGMA table_info(market_daily_v003_states)").fetchall()
    schema = {str(row[1]): str(row[2] or "") for row in rows}
    numeric = {name for name, typ in schema.items() if _sqlite_numeric_type(typ)}
    excludes = set(str(x) for x in rules["explicit_excludes"])

    def prefixed(prefixes: Iterable[str]) -> list[str]:
        out = []
        for name in sorted(numeric):
            if name in excludes:
                continue
            if any(name.startswith(str(p)) for p in prefixes):
                out.append(name)
        return out

    scale = [str(x) for x in rules["scale_exact"] if str(x) in numeric]
    own = prefixed(rules["own_prefixes"])
    cross = prefixed(rules["cross_section_prefixes"])
    sector = prefixed(rules["sector_prefixes"])
    own = sorted(set(own) | set(scale))
    full = sorted(set(own) | set(cross) | set(sector))
    context = sorted(set(cross) | set(sector))
    missing_scale = [x for x in rules["scale_exact"] if x not in numeric]
    return {
        "feature_version": cfg["market_feature_version"],
        "resolution_rule": "schema-only frozen semantic prefix resolution; no outcomes inspected",
        "scale_only": scale,
        "own_state": own,
        "cross_section_context": cross,
        "sector_context": sector,
        "full_endogenous": full,
        "context_union": context,
        "missing_required_scale": missing_scale,
        "counts": {
            "scale_only": len(scale),
            "own_state": len(own),
            "cross_section_context": len(cross),
            "sector_context": len(sector),
            "context_union": len(context),
            "full_endogenous": len(full),
        },
    }


def validate_feature_manifest(manifest: Mapping[str, Any], cfg: Mapping[str, Any]) -> None:
    rules = cfg["feature_resolution"]
    if manifest.get("feature_version") != cfg["market_feature_version"]:
        raise RuntimeError("feature manifest version mismatch")
    if manifest.get("missing_required_scale"):
        raise RuntimeError(f"missing required scale features: {manifest['missing_required_scale']}")
    counts = manifest["counts"]
    if int(counts["scale_only"]) < int(rules["minimum_scale_features"]):
        raise RuntimeError("insufficient scale feature family")
    if int(counts["own_state"]) < int(rules["minimum_own_features"]):
        raise RuntimeError("insufficient own-state feature family")
    if int(counts["context_union"]) < int(rules["minimum_context_features"]):
        raise RuntimeError("insufficient context feature family")


def load_horizon(
    core_db: Path,
    horizon: int,
    cfg: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> pd.DataFrame:
    validate_feature_manifest(manifest, cfg)
    features = list(manifest["full_endogenous"])
    with sqlite3.connect(core_db) as conn:
        state_columns = {str(r[1]) for r in conn.execute("PRAGMA table_info(market_daily_v003_states)")}
        label_columns = {str(r[1]) for r in conn.execute("PRAGMA table_info(market_daily_v003_labels)")}
        required_state = {
            "state_id", "asset_id", "ticker", "sector", "trading_day", "feature_version",
            "state_point_in_time_verified", *features,
        }
        required_label = {
            "state_id", "origin_trading_day", "target_trading_day", "horizon_sessions",
            "return_pct", "corporate_action_overlap", "label_status", "label_version",
        }
        missing = sorted((required_state - state_columns) | (required_label - label_columns))
        if missing:
            raise RuntimeError(f"Core V003 columns missing: {missing}")
        feature_sql = ",\n              ".join(f"s.{name}" for name in features)
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
              {feature_sql},
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
            params=(int(horizon), str(cfg["label_version"]), str(cfg["market_feature_version"])),
        )
    if frame.empty:
        raise RuntimeError(f"no usable rows H{horizon}")
    frame.index = np.arange(len(frame), dtype=int)
    frame["asset_id"] = frame["asset_id"].astype("int32")
    frame["return_pct"] = pd.to_numeric(frame["return_pct"], errors="raise").astype("float64")
    for col in features:
        frame[col] = pd.to_numeric(frame[col], errors="coerce").astype("float64")
    feature_values = frame[features].to_numpy(float)
    if np.isinf(feature_values).any():
        raise RuntimeError("infinite feature values are forbidden")
    if not np.isfinite(frame["return_pct"].to_numpy(float)).all():
        raise RuntimeError("nonfinite target values")
    if (frame[cfg["residual_scale_feature"]].fillna(-1.0) < 0.0).any():
        raise RuntimeError("negative vol63 scale is invalid")
    if (frame["target_trading_day"] <= frame["origin_trading_day"]).any():
        raise RuntimeError("invalid target clock")
    if (frame["corporate_action_overlap"].astype(int) != 0).any():
        raise RuntimeError("usable labels contain corporate action overlap")
    if frame.duplicated(["asset_id", "origin_trading_day"]).any():
        raise RuntimeError("duplicate asset/origin rows")
    if len(frame) < int(cfg["minimum_rows_per_horizon"]):
        raise RuntimeError("minimum row gate failed")
    if frame["asset_id"].nunique() < int(cfg["minimum_assets"]):
        raise RuntimeError("minimum asset gate failed")
    if frame["origin_trading_day"].nunique() < int(cfg["minimum_origin_days"]):
        raise RuntimeError("minimum origin-day gate failed")
    return frame


def origin_day_weights(days: pd.Series) -> np.ndarray:
    counts = days.astype(str).value_counts()
    w = days.astype(str).map(lambda d: 1.0 / float(counts[d])).to_numpy(float)
    return w / float(np.mean(w))


def weighted_quantile(values: np.ndarray, q: float, weights: np.ndarray) -> float:
    x = np.asarray(values, dtype=float)
    w = np.asarray(weights, dtype=float)
    valid = np.isfinite(x) & np.isfinite(w) & (w > 0)
    x, w = x[valid], w[valid]
    if x.size == 0:
        raise RuntimeError("empty weighted quantile")
    order = np.argsort(x, kind="mergesort")
    x, w = x[order], w[order]
    c = np.cumsum(w)
    threshold = float(q) * float(c[-1])
    return float(x[min(int(np.searchsorted(c, threshold, side="left")), len(x) - 1)])


def split_recent_days(
    frame: pd.DataFrame,
    validation_days: int | None = None,
    validation_fraction: float | None = None,
    minimum_train_days: int = 1,
    minimum_validation_days: int = 1,
) -> SplitBundle:
    days = np.array(sorted(frame["origin_trading_day"].astype(str).unique()), dtype=object)
    if validation_days is None:
        if validation_fraction is None:
            raise ValueError("validation_days or validation_fraction required")
        validation_days = max(minimum_validation_days, int(math.ceil(len(days) * validation_fraction)))
    validation_days = int(validation_days)
    if validation_days >= len(days):
        raise RuntimeError("validation consumes all origin days")
    first = str(days[-validation_days])
    val_days = set(str(x) for x in days[-validation_days:])
    validation = frame[frame["origin_trading_day"].astype(str).isin(val_days)].copy()
    train = frame[
        (frame["origin_trading_day"].astype(str) < first)
        & (frame["target_trading_day"].astype(str) < first)
    ].copy()
    if train["origin_trading_day"].nunique() < int(minimum_train_days):
        raise RuntimeError("insufficient purged training days")
    if validation["origin_trading_day"].nunique() < int(minimum_validation_days):
        raise RuntimeError("insufficient validation days")
    return SplitBundle(train=train, validation=validation, first_validation_day=first)


def fit_residual_anchor(train: pd.DataFrame, scale_feature: str, quantiles: Iterable[float]) -> ResidualAnchor:
    y = train["return_pct"].to_numpy(float)
    scale = train[scale_feature].to_numpy(float)
    qs = tuple(float(q) for q in quantiles)
    location = float(np.median(y))
    valid = np.isfinite(scale) & (scale > 0.0)
    if int(valid.sum()) < 1000:
        raise RuntimeError("insufficient positive scale support")
    z = (y[valid] - location) / scale[valid]
    return ResidualAnchor(
        location=location,
        standardized_sorted=np.sort(z),
        standardized_quantiles={q: float(np.quantile(z, q, method="linear")) for q in qs},
        global_return_quantiles={q: float(np.quantile(y, q, method="linear")) for q in qs},
        positive_probability=float(np.mean(y > 0.0)),
    )


def baseline_vol63_bundle(
    anchor: ResidualAnchor,
    test: pd.DataFrame,
    scale_feature: str,
    quantiles: Iterable[float],
) -> dict[str, Any]:
    scale = test[scale_feature].to_numpy(float)
    usable = np.isfinite(scale) & (scale > 0.0)
    predictions: dict[float, np.ndarray] = {}
    for q in quantiles:
        q = float(q)
        out = np.full(len(test), anchor.global_return_quantiles[q], dtype=float)
        out[usable] = anchor.location + anchor.standardized_quantiles[q] * scale[usable]
        predictions[q] = out
    p = np.full(len(test), anchor.positive_probability, dtype=float)
    thresholds = (0.0 - anchor.location) / scale[usable]
    right = np.searchsorted(anchor.standardized_sorted, thresholds, side="right")
    p[usable] = (len(anchor.standardized_sorted) - right) / float(len(anchor.standardized_sorted))
    return {"quantiles": predictions, "probability_positive": p}


def empirical_bundle(train: pd.DataFrame, test: pd.DataFrame, quantiles: Iterable[float]) -> dict[str, Any]:
    y = train["return_pct"].to_numpy(float)
    predictions = {float(q): np.full(len(test), float(np.quantile(y, q, method="linear"))) for q in quantiles}
    p = np.full(len(test), float(np.mean(y > 0.0)))
    return {"quantiles": predictions, "probability_positive": p}


def asset_empirical_bundle(train: pd.DataFrame, test: pd.DataFrame, quantiles: Iterable[float]) -> dict[str, Any]:
    y = train["return_pct"].to_numpy(float)
    grouped = train.groupby("asset_id", sort=False)["return_pct"]
    predictions: dict[float, np.ndarray] = {}
    for q in quantiles:
        q = float(q)
        global_q = float(np.quantile(y, q, method="linear"))
        by_asset = grouped.quantile(q)
        predictions[q] = test["asset_id"].map(by_asset).fillna(global_q).to_numpy(float)
    global_p = float(np.mean(y > 0.0))
    by_asset_p = train.assign(_up=(train["return_pct"] > 0).astype(float)).groupby("asset_id")["_up"].mean()
    p = test["asset_id"].map(by_asset_p).fillna(global_p).to_numpy(float)
    return {"quantiles": predictions, "probability_positive": p}


def _profile_kwargs(profile: Mapping[str, Any], quantile: float) -> dict[str, Any]:
    return {
        "loss": "quantile",
        "quantile": float(quantile),
        "learning_rate": float(profile["learning_rate"]),
        "max_iter": int(profile["max_iter"]),
        "max_leaf_nodes": int(profile["max_leaf_nodes"]),
        "min_samples_leaf": int(profile["min_samples_leaf"]),
        "l2_regularization": float(profile["l2_regularization"]),
        "early_stopping": bool(profile["early_stopping"]),
        "random_state": 42,
    }


def fit_quantile_models(
    train: pd.DataFrame,
    features: list[str],
    scale_feature: str,
    quantiles: Iterable[float],
    profile: Mapping[str, Any],
) -> tuple[ResidualAnchor, dict[float, HistGradientBoostingRegressor]]:
    anchor = fit_residual_anchor(train, scale_feature, quantiles)
    scale = train[scale_feature].to_numpy(float)
    valid = np.isfinite(scale) & (scale > 0.0)
    X = train.loc[valid, features].to_numpy(float)
    z = (train.loc[valid, "return_pct"].to_numpy(float) - anchor.location) / scale[valid]
    w = origin_day_weights(train.loc[valid, "origin_trading_day"])
    models: dict[float, HistGradientBoostingRegressor] = {}
    for q in quantiles:
        model = HistGradientBoostingRegressor(**_profile_kwargs(profile, float(q)))
        model.fit(X, z, sample_weight=w)
        models[float(q)] = model
    return anchor, models


def raw_model_standardized_predictions(
    models: Mapping[float, HistGradientBoostingRegressor],
    frame: pd.DataFrame,
    features: list[str],
    scale_feature: str,
) -> tuple[dict[float, np.ndarray], np.ndarray]:
    scale = frame[scale_feature].to_numpy(float)
    usable = np.isfinite(scale) & (scale > 0.0)
    X = frame[features].to_numpy(float)
    out = {q: np.full(len(frame), np.nan, dtype=float) for q in models}
    if int(usable.sum()) > 0:
        Xu = X[usable]
        for q, model in models.items():
            out[float(q)][usable] = model.predict(Xu)
    return out, usable


def crossing_fraction(qpred: Mapping[float, np.ndarray], usable: np.ndarray) -> float:
    qs = sorted(qpred)
    if int(np.sum(usable)) == 0:
        return 0.0
    matrix = np.column_stack([qpred[q][usable] for q in qs])
    return float(np.mean(np.any(np.diff(matrix, axis=1) < 0.0, axis=1)))


def monotone_rearrange(qpred: Mapping[float, np.ndarray], usable: np.ndarray) -> dict[float, np.ndarray]:
    qs = sorted(qpred)
    out = {q: np.array(qpred[q], dtype=float, copy=True) for q in qs}
    if int(np.sum(usable)) == 0:
        return out
    matrix = np.column_stack([out[q][usable] for q in qs])
    matrix.sort(axis=1)
    for j, q in enumerate(qs):
        out[q][usable] = matrix[:, j]
    return out


def calibration_shifts(
    calibration: pd.DataFrame,
    standardized_predictions: Mapping[float, np.ndarray],
    usable: np.ndarray,
    anchor: ResidualAnchor,
    scale_feature: str,
) -> dict[float, float]:
    scale = calibration[scale_feature].to_numpy(float)
    z = (calibration["return_pct"].to_numpy(float)[usable] - anchor.location) / scale[usable]
    weights = origin_day_weights(calibration.loc[usable, "origin_trading_day"])
    shifts = {}
    for q, pred in standardized_predictions.items():
        residual = z - np.asarray(pred, dtype=float)[usable]
        shifts[float(q)] = weighted_quantile(residual, float(q), weights)
    return shifts


def apply_standardized_calibration(
    qpred: Mapping[float, np.ndarray],
    usable: np.ndarray,
    shifts: Mapping[float, float],
) -> dict[float, np.ndarray]:
    out = {float(q): np.array(v, dtype=float, copy=True) for q, v in qpred.items()}
    for q in out:
        out[q][usable] = out[q][usable] + float(shifts[q])
    return monotone_rearrange(out, usable)


def reconstruct_return_bundle(
    standardized_predictions: Mapping[float, np.ndarray],
    usable: np.ndarray,
    anchor: ResidualAnchor,
    frame: pd.DataFrame,
    scale_feature: str,
) -> dict[str, Any]:
    scale = frame[scale_feature].to_numpy(float)
    predictions: dict[float, np.ndarray] = {}
    for q, zpred in standardized_predictions.items():
        out = np.full(len(frame), anchor.global_return_quantiles[float(q)], dtype=float)
        out[usable] = anchor.location + scale[usable] * np.asarray(zpred, dtype=float)[usable]
        predictions[float(q)] = out
    return {"quantiles": predictions, "probability_positive": np.full(len(frame), anchor.positive_probability)}



def fit_probability_model(
    train: pd.DataFrame,
    features: list[str],
    profile: Mapping[str, Any],
) -> HistGradientBoostingClassifier:
    kwargs = {
        "learning_rate": float(profile["learning_rate"]),
        "max_iter": int(profile["max_iter"]),
        "max_leaf_nodes": int(profile["max_leaf_nodes"]),
        "min_samples_leaf": int(profile["min_samples_leaf"]),
        "l2_regularization": float(profile["l2_regularization"]),
        "early_stopping": bool(profile["early_stopping"]),
        "random_state": 42,
    }
    model = HistGradientBoostingClassifier(loss="log_loss", **kwargs)
    X = train[features].to_numpy(float)
    y = (train["return_pct"].to_numpy(float) > 0.0).astype(int)
    w = origin_day_weights(train["origin_trading_day"])
    model.fit(X, y, sample_weight=w)
    return model


def calibrate_probability_isotonic(
    raw_calibration_probability: np.ndarray,
    calibration: pd.DataFrame,
    raw_test_probability: np.ndarray,
) -> tuple[np.ndarray, dict[str, float]]:
    pcal = np.asarray(raw_calibration_probability, dtype=float)
    ptest = np.asarray(raw_test_probability, dtype=float)
    ycal = (calibration["return_pct"].to_numpy(float) > 0.0).astype(float)
    w = origin_day_weights(calibration["origin_trading_day"])
    if np.unique(pcal).size < 2 or np.unique(ycal).size < 2:
        base = float(np.average(ycal, weights=w))
        return np.full(len(ptest), base, dtype=float), {"method": "weighted_base_rate_fallback", "calibration_base_rate": base}
    iso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
    iso.fit(pcal, ycal, sample_weight=w)
    out = np.clip(iso.predict(ptest), 0.0, 1.0)
    return out, {
        "method": "weighted_isotonic_recent_calibration",
        "raw_calibration_probability_mean": float(np.mean(pcal)),
        "calibrated_test_probability_mean": float(np.mean(out)),
        "calibration_base_rate": float(np.average(ycal, weights=w)),
    }

def constant_standardized_predictions(anchor: ResidualAnchor, frame: pd.DataFrame, scale_feature: str) -> tuple[dict[float, np.ndarray], np.ndarray]:
    scale = frame[scale_feature].to_numpy(float)
    usable = np.isfinite(scale) & (scale > 0.0)
    return ({q: np.full(len(frame), zq, dtype=float) for q, zq in anchor.standardized_quantiles.items()}, usable)
