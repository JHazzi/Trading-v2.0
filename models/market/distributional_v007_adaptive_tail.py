from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from evaluation.market.distributional_v006 import pinball_rows

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CORE_DB = ROOT / "data" / "processed" / "market_daily_v003_core.db"
DEFAULT_CONFIG = ROOT / "config" / "market_brain_distributional_v007.json"

REQUIRED_QUANTILES = (0.05, 0.25, 0.5, 0.75, 0.95)


@dataclass(frozen=True)
class SideParameters:
    alpha: float
    lambda20: float
    kappa: float

    def as_dict(self) -> dict[str, float]:
        return {
            "alpha": float(self.alpha),
            "lambda20": float(self.lambda20),
            "kappa": float(self.kappa),
        }


@dataclass(frozen=True)
class AnchorState:
    global_quantiles: dict[float, float]
    global_median: float
    asset_quantiles: dict[float, pd.Series]
    asset_median_vol20: pd.Series
    asset_median_vol63: pd.Series
    global_median_vol20: float
    global_median_vol63: float


def load_config(path: Path = DEFAULT_CONFIG) -> dict[str, Any]:
    cfg = json.loads(path.read_text(encoding="utf-8"))
    if cfg["version"] != "market_brain_distributional_v007_adaptive_tail_v0011":
        raise ValueError("unexpected V007 version")
    if cfg["model_version"] != (
        "market_brain_distributional_v007_adaptive_asymmetric_scale_v0011"
    ):
        raise ValueError("unexpected V007 model version")
    if cfg["market_feature_version"] != "market_daily_state_v003_core":
        raise ValueError("unexpected market feature version")
    if cfg["label_version"] != "market_daily_reaction_v003_core":
        raise ValueError("unexpected label version")
    if cfg["target"] != "return_pct":
        raise ValueError("V007 target must remain terminal return")
    if tuple(float(x) for x in cfg["quantiles"]) != REQUIRED_QUANTILES:
        raise ValueError("V007 quantile grid changed")
    if cfg["primary_reference"] != "vol63_scaled_empirical":
        raise ValueError("V007 primary reference changed")
    if cfg["primary_candidate"] != "adaptive_asymmetric_asset_scale":
        raise ValueError("V007 primary candidate changed")
    if cfg["location_policy"] != "global_train_median_only":
        raise ValueError("V007 must not introduce directional location learning")
    if tuple(cfg["scale_features"]) != (
        "asset_vol_20d_pct",
        "asset_vol_63d_pct",
    ):
        raise ValueError("V007 scale feature contract changed")
    if cfg.get("strict_historical_pit") is not False:
        raise ValueError("Core V003 is a research reconstruction, not strict PIT")
    for key in (
        "external_proxies_added",
        "event_features_added",
        "graph_features_added",
        "macro_features_added",
        "broker_cost_used_for_training",
        "directional_location_features_added",
    ):
        if cfg.get(key) is not False:
            raise ValueError(f"deferred information enabled: {key}")
    if cfg.get("no_best_horizon_selection") is not True:
        raise ValueError("all horizons must remain reportable")
    if cfg.get("zero_observed_volatility_policy") != (
        "log_ratio_lower_clip_at_max_abs_log_scale_ratio"
    ):
        raise ValueError("unexpected zero observed volatility policy")
    if cfg.get("nonpositive_asset_scale_normalizer_policy") != (
        "global_positive_train_median_fallback"
    ):
        raise ValueError("unexpected asset scale normalizer policy")
    if cfg.get("control_nonpositive_scale_policy") != "global_empirical_fallback":
        raise ValueError("unexpected control nonpositive scale policy")
    if not 0.0 < float(cfg["inner_validation_fraction"]) < 0.5:
        raise ValueError("invalid inner validation fraction")
    for key in ("alpha_grid", "lambda20_grid", "kappa_grid"):
        if not cfg.get(key):
            raise ValueError(f"empty parameter grid: {key}")
    return cfg


def load_horizon(
    core_db: Path,
    horizon: int,
    cfg: dict[str, Any],
) -> pd.DataFrame:
    with sqlite3.connect(core_db) as conn:
        state_columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(market_daily_v003_states)")
        }
        label_columns = {
            str(row[1])
            for row in conn.execute("PRAGMA table_info(market_daily_v003_labels)")
        }
        required_state = {
            "state_id",
            "asset_id",
            "ticker",
            "sector",
            "trading_day",
            "feature_version",
            "state_point_in_time_verified",
            "asset_vol_20d_pct",
            "asset_vol_63d_pct",
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
            """
            SELECT
              s.state_id,
              s.asset_id,
              s.ticker,
              s.sector,
              l.origin_trading_day,
              l.target_trading_day,
              l.return_pct,
              s.asset_vol_20d_pct,
              s.asset_vol_63d_pct,
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
    for column in ("return_pct", "asset_vol_20d_pct", "asset_vol_63d_pct"):
        frame[column] = pd.to_numeric(frame[column], errors="raise").astype("float64")
    numeric = frame[["return_pct", "asset_vol_20d_pct", "asset_vol_63d_pct"]]
    if not np.isfinite(numeric.to_numpy(float)).all():
        raise RuntimeError("nonfinite V007 benchmark data")
    if (frame[["asset_vol_20d_pct", "asset_vol_63d_pct"]] < 0.0).any().any():
        raise RuntimeError("V007 volatility features cannot be negative")
    if (frame["target_trading_day"] <= frame["origin_trading_day"]).any():
        raise RuntimeError("invalid target clock")
    if (frame["corporate_action_overlap"].astype(int) != 0).any():
        raise RuntimeError("usable labels contain corporate-action overlap")
    if int(frame["asset_id"].nunique()) < int(cfg["minimum_assets"]):
        raise RuntimeError("V007 minimum asset gate failed")
    if int(frame["origin_trading_day"].nunique()) < int(cfg["minimum_origin_days"]):
        raise RuntimeError("V007 minimum origin-day gate failed")
    if len(frame) < int(cfg["minimum_rows_per_horizon"]):
        raise RuntimeError("V007 minimum row gate failed")
    return frame


def _quantile(values: np.ndarray, q: float) -> float:
    return float(np.quantile(np.asarray(values, dtype=float), float(q), method="linear"))


def fit_anchor(train: pd.DataFrame, quantiles: Iterable[float]) -> AnchorState:
    qs = tuple(float(q) for q in quantiles)
    y = train["return_pct"].to_numpy(float)
    global_quantiles = {q: _quantile(y, q) for q in qs}
    grouped = train.groupby("asset_id", sort=False)["return_pct"]
    asset_quantiles = {q: grouped.quantile(q) for q in qs}
    vol_group = train.groupby("asset_id", sort=False)
    asset_vol20 = vol_group["asset_vol_20d_pct"].median()
    asset_vol63 = vol_group["asset_vol_63d_pct"].median()

    def _global_positive_fallback(column: str) -> float:
        values = train[column].to_numpy(float)
        ordinary = float(np.median(values))
        if ordinary > 0.0:
            return ordinary
        positive = values[values > 0.0]
        if positive.size == 0:
            raise RuntimeError(f"no positive training support for {column}")
        return float(np.median(positive))

    return AnchorState(
        global_quantiles=global_quantiles,
        global_median=global_quantiles[0.5],
        asset_quantiles=asset_quantiles,
        asset_median_vol20=asset_vol20,
        asset_median_vol63=asset_vol63,
        global_median_vol20=_global_positive_fallback("asset_vol_20d_pct"),
        global_median_vol63=_global_positive_fallback("asset_vol_63d_pct"),
    )


def _map_with_fallback(
    asset_ids: pd.Series,
    values: pd.Series,
    fallback: float,
) -> np.ndarray:
    return asset_ids.map(values).fillna(float(fallback)).to_numpy(float)


def _asset_tail_widths(
    test: pd.DataFrame,
    anchor: AnchorState,
    q: float,
) -> np.ndarray:
    aq = _map_with_fallback(
        test["asset_id"], anchor.asset_quantiles[q], anchor.global_quantiles[q]
    )
    am = _map_with_fallback(
        test["asset_id"], anchor.asset_quantiles[0.5], anchor.global_median
    )
    width = aq - am
    if q < 0.5 and np.any(width > 1e-12):
        raise RuntimeError("lower asset quantile width became positive")
    if q > 0.5 and np.any(width < -1e-12):
        raise RuntimeError("upper asset quantile width became negative")
    return width


def _log_scale_state(
    test: pd.DataFrame,
    anchor: AnchorState,
    lambda20: float,
    max_abs_log_ratio: float,
) -> np.ndarray:
    med20 = _map_with_fallback(
        test["asset_id"], anchor.asset_median_vol20, anchor.global_median_vol20
    )
    med63 = _map_with_fallback(
        test["asset_id"], anchor.asset_median_vol63, anchor.global_median_vol63
    )
    # A zero rolling standard deviation is a valid market state (a perfectly
    # flat window), not missing data.  The mathematical model already clips
    # log scale ratios to +/- max_abs_log_ratio, so log(0) has the natural
    # limiting representation -max_abs_log_ratio.  A zero/degenerate TRAIN
    # median cannot normalize an asset, therefore it falls back to the
    # positive global TRAIN median.  No test outcome is used here.
    med20 = np.where(med20 > 0.0, med20, float(anchor.global_median_vol20))
    med63 = np.where(med63 > 0.0, med63, float(anchor.global_median_vol63))
    if np.any(med20 <= 0.0) or np.any(med63 <= 0.0):
        raise RuntimeError("no positive training volatility normalizer")
    v20 = test["asset_vol_20d_pct"].to_numpy(float)
    v63 = test["asset_vol_63d_pct"].to_numpy(float)
    if np.any(v20 < 0.0) or np.any(v63 < 0.0):
        raise RuntimeError("volatility cannot be negative")
    r20 = v20 / med20
    r63 = v63 / med63
    limit = float(max_abs_log_ratio)

    def _bounded_log_ratio(ratio: np.ndarray) -> np.ndarray:
        out = np.full(len(ratio), -limit, dtype=float)
        positive = ratio > 0.0
        out[positive] = np.clip(np.log(ratio[positive]), -limit, limit)
        return out

    l20 = _bounded_log_ratio(r20)
    l63 = _bounded_log_ratio(r63)
    w20 = float(lambda20)
    return w20 * l20 + (1.0 - w20) * l63


def side_multiplier(
    test: pd.DataFrame,
    anchor: AnchorState,
    params: SideParameters,
    max_abs_log_ratio: float,
) -> np.ndarray:
    state = _log_scale_state(
        test, anchor, params.lambda20, max_abs_log_ratio
    )
    return float(params.kappa) * np.exp(float(params.alpha) * state)


def predict_adaptive_distribution(
    test: pd.DataFrame,
    anchor: AnchorState,
    lower: SideParameters,
    upper: SideParameters,
    cfg: dict[str, Any],
) -> dict[str, object]:
    quantiles = tuple(float(q) for q in cfg["quantiles"])
    n = len(test)
    center = np.full(n, float(anchor.global_median), dtype=float)
    lo_mult = side_multiplier(
        test, anchor, lower, float(cfg["max_abs_log_scale_ratio"])
    )
    hi_mult = side_multiplier(
        test, anchor, upper, float(cfg["max_abs_log_scale_ratio"])
    )
    predictions: dict[float, np.ndarray] = {}
    for q in quantiles:
        if q < 0.5:
            predictions[q] = center + _asset_tail_widths(test, anchor, q) * lo_mult
        elif q > 0.5:
            predictions[q] = center + _asset_tail_widths(test, anchor, q) * hi_mult
        else:
            predictions[q] = center.copy()
    matrix = np.column_stack([predictions[q] for q in quantiles])
    if np.any(np.diff(matrix, axis=1) < -1e-10):
        raise RuntimeError("adaptive V007 produced quantile crossing")
    probability_positive = probability_positive_from_quantiles(
        predictions, quantiles
    )
    return {
        "quantiles": predictions,
        "probability_positive": probability_positive,
    }


def probability_positive_from_quantiles(
    predictions: dict[float, np.ndarray],
    quantiles: Iterable[float],
) -> np.ndarray:
    qs = np.asarray(tuple(float(q) for q in quantiles), dtype=float)
    matrix = np.column_stack([np.asarray(predictions[float(q)], dtype=float) for q in qs])
    out = np.empty(len(matrix), dtype=float)
    for i, row in enumerate(matrix):
        # np.interp returns the CDF level at return=0 under piecewise-linear quantiles.
        cdf0 = float(np.interp(0.0, row, qs, left=0.0, right=1.0))
        out[i] = 1.0 - cdf0
    return np.clip(out, 0.0, 1.0)


def _global_empirical_bundle(
    train: pd.DataFrame,
    test: pd.DataFrame,
    quantiles: Iterable[float],
) -> dict[str, object]:
    qs = tuple(float(q) for q in quantiles)
    y = train["return_pct"].to_numpy(float)
    pred = {q: np.full(len(test), _quantile(y, q), dtype=float) for q in qs}
    return {
        "quantiles": pred,
        "probability_positive": np.full(len(test), float(np.mean(y > 0.0))),
    }


def _asset_empirical_bundle(
    train: pd.DataFrame,
    test: pd.DataFrame,
    quantiles: Iterable[float],
) -> dict[str, object]:
    qs = tuple(float(q) for q in quantiles)
    y = train["return_pct"].to_numpy(float)
    grouped = train.groupby("asset_id", sort=False)["return_pct"]
    pred: dict[float, np.ndarray] = {}
    for q in qs:
        by_asset = grouped.quantile(q)
        pred[q] = _map_with_fallback(test["asset_id"], by_asset, _quantile(y, q))
    pos = (
        train.assign(_positive=train["return_pct"] > 0.0)
        .groupby("asset_id", sort=False)["_positive"]
        .mean()
    )
    return {
        "quantiles": pred,
        "probability_positive": _map_with_fallback(
            test["asset_id"], pos, float(np.mean(y > 0.0))
        ),
    }


def _vol_scaled_bundle(
    train: pd.DataFrame,
    test: pd.DataFrame,
    quantiles: Iterable[float],
    scale_column: str,
) -> dict[str, object]:
    qs = tuple(float(q) for q in quantiles)
    y = train["return_pct"].to_numpy(float)
    scale_train = train[scale_column].to_numpy(float)
    scale_test = test[scale_column].to_numpy(float)
    if np.any(scale_train < 0.0) or np.any(scale_test < 0.0):
        raise RuntimeError("volatility control cannot use negative scale")
    valid_train = scale_train > 0.0
    if int(np.sum(valid_train)) < len(qs) + 1:
        raise RuntimeError("insufficient positive volatility-scale support")
    location = float(np.median(y))
    standardized = (y[valid_train] - location) / scale_train[valid_train]
    standardized_sorted = np.sort(standardized)
    zq = np.quantile(standardized, qs, method="linear")

    # Match the completed V006 nonpositive-scale contract: zero-scale TEST
    # rows receive the global empirical distribution rather than being
    # removed or assigned an invented epsilon.  The same rule is extended
    # prospectively to the vol63 control.
    global_bundle = _global_empirical_bundle(train, test, qs)
    usable_test = scale_test > 0.0
    pred: dict[float, np.ndarray] = {}
    for q, value in zip(qs, zq):
        values = np.asarray(global_bundle["quantiles"][q], dtype=float).copy()
        values[usable_test] = location + float(value) * scale_test[usable_test]
        pred[q] = values
    prob = np.asarray(global_bundle["probability_positive"], dtype=float).copy()
    thresholds = (0.0 - location) / scale_test[usable_test]
    right = np.searchsorted(standardized_sorted, thresholds, side="right")
    prob[usable_test] = (
        len(standardized_sorted) - right
    ) / float(len(standardized_sorted))
    return {"quantiles": pred, "probability_positive": prob}


def fit_predict_controls(
    train: pd.DataFrame,
    test: pd.DataFrame,
    cfg: dict[str, Any],
) -> dict[str, dict[str, object]]:
    qs = tuple(float(q) for q in cfg["quantiles"])
    return {
        "train_empirical": _global_empirical_bundle(train, test, qs),
        "asset_empirical": _asset_empirical_bundle(train, test, qs),
        "vol20_scaled_empirical": _vol_scaled_bundle(
            train, test, qs, "asset_vol_20d_pct"
        ),
        "vol63_scaled_empirical": _vol_scaled_bundle(
            train, test, qs, "asset_vol_63d_pct"
        ),
    }


def build_inner_temporal_split(
    outer_train: pd.DataFrame,
    cfg: dict[str, Any],
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    days = np.asarray(sorted(outer_train["origin_trading_day"].astype(str).unique()))
    validation_days = max(
        int(math.ceil(len(days) * float(cfg["inner_validation_fraction"]))),
        int(cfg["minimum_inner_validation_origin_days"]),
    )
    if len(days) - validation_days < int(cfg["minimum_inner_train_origin_days"]):
        raise RuntimeError("insufficient inner train origin days")
    first_val_day = str(days[-validation_days])
    inner_val = outer_train[
        outer_train["origin_trading_day"].astype(str) >= first_val_day
    ].copy()
    inner_train = outer_train[
        (outer_train["origin_trading_day"].astype(str) < first_val_day)
        & (outer_train["target_trading_day"].astype(str) < first_val_day)
    ].copy()
    if inner_train.empty or inner_val.empty:
        raise RuntimeError("empty nested temporal split")
    if not (
        inner_train["target_trading_day"].astype(str) < first_val_day
    ).all():
        raise RuntimeError("inner selection leakage: target reaches validation")
    return inner_train, inner_val, {
        "first_validation_day": first_val_day,
        "last_validation_day": str(inner_val["origin_trading_day"].max()),
        "inner_train_rows": int(len(inner_train)),
        "inner_validation_rows": int(len(inner_val)),
        "inner_train_origin_days": int(inner_train["origin_trading_day"].nunique()),
        "inner_validation_origin_days": int(inner_val["origin_trading_day"].nunique()),
    }


def _daily_equal_side_score(
    actual: np.ndarray,
    origin_days: pd.Series,
    predictions: dict[float, np.ndarray],
    quantiles: Iterable[float],
) -> float:
    qs = tuple(float(q) for q in quantiles)
    row_loss = np.mean(
        np.column_stack([
            pinball_rows(actual, predictions[q], q) for q in qs
        ]),
        axis=1,
    )
    daily = pd.DataFrame({
        "origin_trading_day": origin_days.astype(str).to_numpy(),
        "loss": row_loss,
    }).groupby("origin_trading_day", sort=True)["loss"].mean()
    return float(daily.mean())


def parameter_grid(cfg: dict[str, Any]) -> list[SideParameters]:
    grid = [
        SideParameters(float(alpha), float(lam), float(kappa))
        for alpha in cfg["alpha_grid"]
        for lam in cfg["lambda20_grid"]
        for kappa in cfg["kappa_grid"]
    ]
    return sorted(
        grid,
        key=lambda p: (
            p.alpha,
            abs(p.lambda20 - 0.5),
            p.lambda20,
            abs(p.kappa - 1.0),
            p.kappa,
        ),
    )


def select_side_parameters(
    inner_train: pd.DataFrame,
    inner_validation: pd.DataFrame,
    side: str,
    cfg: dict[str, Any],
) -> tuple[SideParameters, pd.DataFrame]:
    if side not in {"lower", "upper"}:
        raise ValueError("side must be lower or upper")
    side_qs = tuple(float(q) for q in cfg[f"{side}_quantiles"])
    anchor = fit_anchor(inner_train, cfg["quantiles"])
    actual = inner_validation["return_pct"].to_numpy(float)
    rows = []
    for params in parameter_grid(cfg):
        # The unused side is irrelevant; using identical params keeps the full
        # distribution valid while scoring only the requested side.
        bundle = predict_adaptive_distribution(
            inner_validation, anchor, params, params, cfg
        )
        score = _daily_equal_side_score(
            actual,
            inner_validation["origin_trading_day"],
            bundle["quantiles"],
            side_qs,
        )
        rows.append({**params.as_dict(), "score": score})
    table = pd.DataFrame(rows).sort_values(
        ["score", "alpha", "lambda20", "kappa"], kind="mergesort"
    ).reset_index(drop=True)
    best = table.iloc[0]
    return (
        SideParameters(
            alpha=float(best["alpha"]),
            lambda20=float(best["lambda20"]),
            kappa=float(best["kappa"]),
        ),
        table,
    )


def select_nested_parameters(
    outer_train: pd.DataFrame,
    cfg: dict[str, Any],
) -> tuple[SideParameters, SideParameters, dict[str, Any], dict[str, pd.DataFrame]]:
    inner_train, inner_val, split = build_inner_temporal_split(outer_train, cfg)
    lower, lower_table = select_side_parameters(inner_train, inner_val, "lower", cfg)
    upper, upper_table = select_side_parameters(inner_train, inner_val, "upper", cfg)
    meta = {
        "split": split,
        "lower_selected": lower.as_dict(),
        "upper_selected": upper.as_dict(),
        "selection_rule": (
            "minimize inner origin-day-equal pinball separately over q05/q25 and q75/q95; "
            "q50 remains global train median"
        ),
    }
    return lower, upper, meta, {
        "lower_grid": lower_table,
        "upper_grid": upper_table,
    }
