from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import Ridge, SGDRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from evaluation.market.daily_v003_benchmark import (
    build_purged_day_folds,
    daily_cross_section_diagnostics,
    fold_summary,
    metrics,
    moving_block_bootstrap_days,
    paired_point,
)
from features.market.daily_v003_core import (
    ALL_FEATURES,
    CROSS_FEATURES,
    OWN_FEATURES,
    SECTOR_FEATURES,
)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CORE_DB = ROOT / "data" / "processed" / "market_daily_v003_core.db"
DEFAULT_CONFIG = ROOT / "config" / "market_brain_daily_v003_benchmark.json"

TARGET = "return_pct"
META_COLS = [
    "state_id",
    "asset_id",
    "ticker",
    "sector",
    "origin_trading_day",
    "target_trading_day",
]


def load_config(path: Path = DEFAULT_CONFIG) -> dict[str, Any]:
    cfg = json.loads(path.read_text(encoding="utf-8"))
    if cfg.get("no_hyperparameter_tuning") is not True:
        raise ValueError("benchmark must remain preregistered")
    if cfg.get("primary_model") != "hgb_full":
        raise ValueError("primary model changed")
    if cfg.get("primary_baseline") != "train_median":
        raise ValueError("primary baseline changed")
    return cfg


def load_horizon(core_db: Path, horizon: int) -> pd.DataFrame:
    with sqlite3.connect(core_db) as conn:
        states_cols = {
            str(r[1]) for r in conn.execute(
                "PRAGMA table_info(market_daily_v003_states)"
            )
        }
        required = set(ALL_FEATURES) | {
            "state_id", "asset_id", "ticker", "sector", "trading_day"
        }
        missing = sorted(required - states_cols)
        if missing:
            raise RuntimeError(f"core state columns missing: {missing}")

        select_features = ",\n                ".join(
            f"s.{c} AS {c}" for c in ALL_FEATURES
        )
        q = f"""
        SELECT
            s.state_id,
            s.asset_id,
            s.ticker,
            s.sector,
            l.origin_trading_day,
            l.target_trading_day,
            l.return_pct,
            {select_features}
        FROM market_daily_v003_labels l
        JOIN market_daily_v003_states s
          ON s.state_id=l.state_id
        WHERE l.horizon_sessions=?
          AND l.label_status='usable'
        ORDER BY l.origin_trading_day, s.asset_id
        """
        frame = pd.read_sql_query(q, conn, params=(int(horizon),))

    if frame.empty:
        raise RuntimeError(f"no usable rows for H{horizon}")

    frame.index = np.arange(len(frame), dtype=int)
    numeric = [TARGET] + ALL_FEATURES
    for col in numeric:
        frame[col] = pd.to_numeric(frame[col], errors="raise").astype("float32")
    frame["asset_id"] = frame["asset_id"].astype("int32")

    if not np.isfinite(frame[numeric].to_numpy(dtype="float32")).all():
        raise RuntimeError("nonfinite benchmark data")
    return frame


def train_baselines(train: pd.DataFrame, test: pd.DataFrame, horizon: int) -> dict[str, np.ndarray]:
    y = train[TARGET].to_numpy(float)
    global_mean = float(np.mean(y))
    global_median = float(np.median(y))
    asset_means = train.groupby("asset_id")[TARGET].mean()

    momentum_col = f"asset_return_{horizon}d_pct"
    if momentum_col not in test.columns:
        raise RuntimeError(f"missing momentum baseline feature: {momentum_col}")

    return {
        "pred_zero": np.zeros(len(test), dtype=float),
        "pred_train_mean": np.full(len(test), global_mean, dtype=float),
        "pred_train_median": np.full(len(test), global_median, dtype=float),
        "pred_asset_train_mean": (
            test["asset_id"].map(asset_means).fillna(global_mean).to_numpy(float)
        ),
        "pred_momentum_same_h": test[momentum_col].to_numpy(float),
    }


def _linear(kind: str, cfg: dict[str, Any]) -> Pipeline:
    if kind == "ridge_full":
        model = Ridge(alpha=float(cfg["models"][kind]["alpha"]))
    elif kind == "sgd_huber_full":
        m = cfg["models"][kind]
        model = SGDRegressor(
            loss="huber",
            alpha=float(m["alpha"]),
            epsilon=float(m["epsilon"]),
            max_iter=int(m["max_iter"]),
            tol=float(m["tol"]),
            random_state=int(cfg["random_seed"]),
            shuffle=False,
            average=True,
        )
    else:
        raise ValueError(kind)
    return Pipeline([
        ("scale", StandardScaler()),
        ("model", model),
    ])


def _hgb(kind: str, cfg: dict[str, Any]) -> HistGradientBoostingRegressor:
    m = cfg["models"][kind]
    return HistGradientBoostingRegressor(
        loss=str(m["loss"]),
        learning_rate=float(m["learning_rate"]),
        max_iter=int(m["max_iter"]),
        max_leaf_nodes=int(m["max_leaf_nodes"]),
        min_samples_leaf=int(m["min_samples_leaf"]),
        l2_regularization=float(m["l2_regularization"]),
        random_state=int(cfg["random_seed"]),
    )


def fit_predict_models(
    train: pd.DataFrame,
    test: pd.DataFrame,
    cfg: dict[str, Any],
) -> dict[str, np.ndarray]:
    out = {}

    for kind in ("ridge_full", "sgd_huber_full"):
        model = _linear(kind, cfg)
        model.fit(train[ALL_FEATURES], train[TARGET])
        out[f"pred_{kind}"] = model.predict(test[ALL_FEATURES])

    groups = {
        "hgb_own": OWN_FEATURES,
        "hgb_own_cross": OWN_FEATURES + CROSS_FEATURES,
        "hgb_full": ALL_FEATURES,
    }
    for kind, features in groups.items():
        model = _hgb(kind, cfg)
        model.fit(train[features], train[TARGET])
        out[f"pred_{kind}"] = model.predict(test[features])

    return out


def run_horizon(
    core_db: Path,
    horizon: int,
    cfg: dict[str, Any],
) -> tuple[dict[str, Any], pd.DataFrame]:
    frame = load_horizon(core_db, horizon)
    folds = build_purged_day_folds(
        frame,
        n_folds=int(cfg["outer_folds"]),
        initial_fraction=float(cfg["initial_fraction"]),
    )

    parts = []
    fold_results = []
    for fold in folds:
        train = frame.loc[list(fold.train_index)].copy()
        test = frame.loc[list(fold.test_index)].copy()

        preds = train_baselines(train, test, horizon)
        preds.update(fit_predict_models(train, test, cfg))
        for name, pred in preds.items():
            test[name] = np.asarray(pred, dtype="float32")
        test["fold_id"] = int(fold.fold_id)
        parts.append(test)

        fold_metrics = {}
        for col in [c for c in test.columns if c.startswith("pred_")]:
            fold_metrics[col.removeprefix("pred_")] = metrics(
                test[TARGET].to_numpy(float),
                test[col].to_numpy(float),
            )
        fold_results.append({
            "fold_id": fold.fold_id,
            "first_test_day": fold.first_test_day,
            "last_test_day": fold.last_test_day,
            "train_rows": len(train),
            "test_rows": len(test),
            "metrics": fold_metrics,
            "primary_delta": paired_point(
                test,
                baseline_col="pred_train_median",
                candidate_col="pred_hgb_full",
                target_col=TARGET,
            ),
            "context_increment": {
                "own_to_cross": paired_point(
                    test,
                    baseline_col="pred_hgb_own",
                    candidate_col="pred_hgb_own_cross",
                    target_col=TARGET,
                ),
                "cross_to_full_sector": paired_point(
                    test,
                    baseline_col="pred_hgb_own_cross",
                    candidate_col="pred_hgb_full",
                    target_col=TARGET,
                ),
            },
        })

    oos = pd.concat(parts, ignore_index=True)
    pred_cols = [c for c in oos.columns if c.startswith("pred_")]

    pooled = {}
    for col in pred_cols:
        name = col.removeprefix("pred_")
        pooled[name] = {
            "metrics": metrics(
                oos[TARGET].to_numpy(float),
                oos[col].to_numpy(float),
            ),
            "cross_section": daily_cross_section_diagnostics(
                oos,
                target_col=TARGET,
                pred_col=col,
            ),
        }

    comparisons = {
        "primary_train_median_vs_hgb_full": paired_point(
            oos,
            baseline_col="pred_train_median",
            candidate_col="pred_hgb_full",
            target_col=TARGET,
        ),
        "asset_mean_vs_hgb_full": paired_point(
            oos,
            baseline_col="pred_asset_train_mean",
            candidate_col="pred_hgb_full",
            target_col=TARGET,
        ),
        "momentum_vs_hgb_full": paired_point(
            oos,
            baseline_col="pred_momentum_same_h",
            candidate_col="pred_hgb_full",
            target_col=TARGET,
        ),
        "hgb_own_vs_own_cross": paired_point(
            oos,
            baseline_col="pred_hgb_own",
            candidate_col="pred_hgb_own_cross",
            target_col=TARGET,
        ),
        "hgb_own_cross_vs_full": paired_point(
            oos,
            baseline_col="pred_hgb_own_cross",
            candidate_col="pred_hgb_full",
            target_col=TARGET,
        ),
    }

    block = {}
    for length in cfg["moving_block_lengths_origin_days"]:
        block[str(length)] = moving_block_bootstrap_days(
            oos,
            baseline_col="pred_train_median",
            candidate_col="pred_hgb_full",
            target_col=TARGET,
            block_length=int(length),
            reps=int(cfg["bootstrap_reps"]),
            seed=int(cfg["random_seed"]) + horizon * 100 + int(length),
        )

    result = {
        "horizon_sessions": int(horizon),
        "dataset_rows": int(len(frame)),
        "dataset_assets": int(frame["asset_id"].nunique()),
        "dataset_origin_days": int(frame["origin_trading_day"].nunique()),
        "dataset_first_origin_day": str(frame["origin_trading_day"].min()),
        "dataset_last_origin_day": str(frame["origin_trading_day"].max()),
        "folds": fold_summary(folds),
        "fold_results": fold_results,
        "pooled": pooled,
        "comparisons": comparisons,
        "primary_moving_block_bootstrap": block,
        "scientific_contract": {
            "primary_model": cfg["primary_model"],
            "primary_baseline": cfg["primary_baseline"],
            "best_model_selection_for_primary_claim": False,
            "hyperparameter_tuning": False,
            "row_level_iid_inference": False,
        },
    }
    return result, oos
