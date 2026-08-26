from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

from evaluation.market.daily_v0052_financial_conditions_benchmark import (
    metrics, paired, moving_block_bootstrap_days, load_boundaries, masks,
)
from models.market.daily_v004_factorized_benchmark import load_frames as load_v004_frames

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "config" / "market_brain_daily_v0052_financial_conditions.json"


def load_config(path: Path = DEFAULT_CONFIG) -> dict:
    cfg = json.loads(path.read_text(encoding="utf-8"))
    b = cfg["benchmark"]
    if b["primary_candidate"] != "full_financial_conditions":
        raise ValueError("V005.2 primary changed")
    if not b["do_not_rescue_primary_with_secondary_ablation"]:
        raise ValueError("secondary ablations cannot rescue primary")
    if not b["do_not_tune_after_results"]:
        raise ValueError("post-result tuning unexpectedly enabled")
    if cfg["rejected_not_stacked"] != "V005.1 SPY_QQQ_IWM":
        raise ValueError("V005.1 stacking contract changed")
    return cfg


def hgb(params):
    return HistGradientBoostingRegressor(
        loss=params["loss"], learning_rate=params["learning_rate"],
        max_iter=params["max_iter"], max_leaf_nodes=params["max_leaf_nodes"],
        min_samples_leaf=params["min_samples_leaf"],
        l2_regularization=params["l2_regularization"],
        early_stopping=params["early_stopping"], random_state=params["random_state"],
    )


def fit_hgb(train, test, features, target, params):
    model = hgb(params)
    model.fit(train[features], train[target])
    return model.predict(test[features])


def load_v004_oos(path: Path):
    x = pd.read_csv(path)
    required = ["state_id", "pred_train_median", "pred_hgb_full", "pred_additive_hgb"]
    missing = [c for c in required if c not in x.columns]
    if missing:
        raise RuntimeError(f"V004 OOS missing columns: {missing}")
    return x[required].rename(columns={"pred_additive_hgb": "pred_v004_additive_hgb"})


def load_financial_state(db: Path, pure_features):
    with sqlite3.connect(db) as conn:
        x = pd.read_sql_query(
            "SELECT * FROM market_financial_conditions_v0052 ORDER BY trading_day", conn
        )
    if x.empty:
        raise RuntimeError("V005.2 financial state empty")
    x["origin_trading_day"] = x["trading_day"].astype(str)
    if x["origin_trading_day"].duplicated().any():
        raise RuntimeError("duplicate V005.2 financial state day")
    if int(x["vix_feature_lag_sessions"].max()) != 1:
        raise RuntimeError("VIX lag causal guard failed")
    if int(x["adjusted_close_used"].max()) != 0:
        raise RuntimeError("adjusted close causal guard failed")
    return x[["origin_trading_day", *pure_features]].copy()


def load_frames(math_db: Path, state_db: Path, horizon: int, cfg: dict):
    market, sector, asset, sf, aaf, daf = load_v004_frames(math_db, horizon)
    pure = sum(cfg["pure_external_features"].values(), [])
    ext = load_financial_state(state_db, pure)
    before = len(market)
    market = market.merge(ext, on="origin_trading_day", how="left", validate="one_to_one")
    if len(market) != before:
        raise RuntimeError("financial state merge changed market row count")

    # VIX is annualized implied volatility. V004 realized vol is a daily-return
    # standard deviation in percentage points; annualize only for this comparison.
    market["vix_lag1_minus_market_ann_realized_vol_20d_pct"] = (
        market["vix_lag1_close"] - market["market_realized_vol_20d_pct"] * np.sqrt(252.0)
    )
    full_external = [*pure, *cfg["interaction_features"]]
    market["financial_state_complete"] = np.isfinite(
        market[full_external].to_numpy(float)
    ).all(axis=1).astype(int)
    return market, sector, asset, sf, aaf, daf


def run_horizon(math_db: Path, state_db: Path, v004_dir: Path, horizon: int, cfg: dict):
    market, sector, asset, sf, aaf, _ = load_frames(math_db, state_db, horizon, cfg)
    boundaries = load_boundaries(v004_dir / f"h{horizon}_factorized_benchmark.json")
    old = load_v004_oos(v004_dir / f"h{horizon}_factorized_oos.csv.gz")

    base = cfg["benchmark"]["base_market_features"]
    vix = [*cfg["pure_external_features"]["vix"], *cfg["interaction_features"]]
    rates = cfg["pure_external_features"]["rates"]
    credit = cfg["pure_external_features"]["credit"]
    full = [*vix, *rates, *credit]
    candidates = {
        "full_financial_conditions": [*base, *full],
        "vix_only": [*base, *vix],
        "rates_only": [*base, *rates],
        "credit_only": [*base, *credit],
    }

    parts, component = [], []
    for b in boundaries:
        mtr, mte = masks(market, b)
        str_, ste = masks(sector, b)
        atr, ate = masks(asset, b)
        mt, mv = market[mtr].copy(), market[mte].copy()
        st, sv = sector[str_].copy(), sector[ste].copy()
        at, av = asset[atr].copy(), asset[ate].copy()

        if not bool(mt["financial_state_complete"].all()):
            raise RuntimeError(f"incomplete V005.2 train state fold={b['fold_id']}")
        if not bool(mv["financial_state_complete"].all()):
            raise RuntimeError(f"incomplete V005.2 test state fold={b['fold_id']}")

        mv["pred_market_v004_replay"] = fit_hgb(
            mt, mv, base, "target_market", cfg["benchmark"]["market_hgb"]
        )
        for name, features in candidates.items():
            mv[f"pred_market_{name}"] = fit_hgb(
                mt, mv, features, "target_market", cfg["benchmark"]["market_hgb"]
            )

        sv["pred_sector_shared"] = fit_hgb(
            st, sv, sf, "target_sector", cfg["benchmark"]["sector_hgb"]
        )
        av["pred_asset_shared"] = fit_hgb(
            at, av, aaf, "target_asset_additive_residual_pct", cfg["benchmark"]["asset_hgb"]
        )

        market_cols = [
            "origin_trading_day", "pred_market_v004_replay",
            *[f"pred_market_{name}" for name in candidates],
        ]
        av = av.merge(mv[market_cols], on="origin_trading_day", how="inner", validate="many_to_one")
        av = av.merge(
            sv[["origin_trading_day", "sector", "pred_sector_shared"]],
            on=["origin_trading_day", "sector"], how="inner", validate="many_to_one"
        )
        av = av.merge(old, on="state_id", how="inner", validate="one_to_one")

        av["pred_v004_replay"] = av["pred_market_v004_replay"] + av["pred_sector_shared"] + av["pred_asset_shared"]
        for name in candidates:
            av[f"pred_{name}"] = av[f"pred_market_{name}"] + av["pred_sector_shared"] + av["pred_asset_shared"]
        av["fold_id"] = int(b["fold_id"])

        parity = float(np.max(np.abs(
            av["pred_v004_replay"].to_numpy(float) - av["pred_v004_additive_hgb"].to_numpy(float)
        )))
        if parity > 1e-9:
            raise AssertionError(
                f"V004 replay parity failure fold={b['fold_id']} max_abs={parity}"
            )

        component.append({
            "fold_id": int(b["fold_id"]),
            "first_test_day": b["first_test_day"],
            "last_test_day": b["last_test_day"],
            "market_v004": metrics(mv["target_market"], mv["pred_market_v004_replay"]),
            "market_full_financial_conditions": metrics(mv["target_market"], mv["pred_market_full_financial_conditions"]),
            "market_vix_only": metrics(mv["target_market"], mv["pred_market_vix_only"]),
            "market_rates_only": metrics(mv["target_market"], mv["pred_market_rates_only"]),
            "market_credit_only": metrics(mv["target_market"], mv["pred_market_credit_only"]),
            "v004_replay_max_abs_prediction_error": parity,
        })
        parts.append(av)

    oos = pd.concat(parts, ignore_index=True)
    if len(oos) != len(old):
        raise AssertionError("V005.2 OOS row count differs from V004")
    if set(oos["state_id"].astype(str)) != set(old["state_id"].astype(str)):
        raise AssertionError("V005.2 OOS state set differs from V004")

    primary_col = "pred_full_financial_conditions"
    primary = {
        "metrics": metrics(oos["return_pct"], oos[primary_col]),
        "increment_vs_v004": paired(oos, "pred_v004_additive_hgb", primary_col),
        "absolute_vs_train_median": paired(oos, "pred_train_median", primary_col),
        "vs_v003_hgb_full": paired(oos, "pred_hgb_full", primary_col),
    }
    secondary = {}
    for name in ("vix_only", "rates_only", "credit_only"):
        col = f"pred_{name}"
        secondary[name] = {
            "metrics": metrics(oos["return_pct"], oos[col]),
            "increment_vs_v004": paired(oos, "pred_v004_additive_hgb", col),
            "claim_boundary": "Diagnostic only; cannot rescue a failed primary.",
        }

    bcfg = cfg["benchmark"]["bootstrap"]
    primary_bootstrap, absolute_bootstrap, secondary_bootstrap = {}, {}, {}
    for L in bcfg["moving_block_lengths_origin_days"]:
        primary_bootstrap[str(L)] = moving_block_bootstrap_days(
            oos, "pred_v004_additive_hgb", primary_col, "return_pct", L,
            bcfg["reps"], bcfg["seed"] + 1000 * horizon + L,
        )
        absolute_bootstrap[str(L)] = moving_block_bootstrap_days(
            oos, "pred_train_median", primary_col, "return_pct", L,
            bcfg["reps"], bcfg["seed"] + 2000 * horizon + L,
        )
        for idx, name in enumerate(("vix_only", "rates_only", "credit_only"), start=1):
            secondary_bootstrap.setdefault(name, {})[str(L)] = moving_block_bootstrap_days(
                oos, "pred_v004_additive_hgb", f"pred_{name}", "return_pct", L,
                bcfg["reps"], bcfg["seed"] + 3000 * horizon + 100 * idx + L,
            )

    return {
        "benchmark_version": cfg["version"],
        "horizon_sessions": int(horizon),
        "oos_rows": int(len(oos)),
        "oos_assets": int(oos["asset_id"].nunique()),
        "oos_origin_days": int(oos["origin_trading_day"].nunique()),
        "feature_contract": {
            "base_market_features": len(base),
            "vix_features_including_interaction": len(vix),
            "rates_features": len(rates),
            "credit_features": len(credit),
            "primary_external_features": len(full),
            "primary_market_features_total": len(base) + len(full),
            "sector_model_changed": False,
            "asset_model_changed": False,
            "SPY_QQQ_IWM_stacked": False,
        },
        "component_fold_metrics": component,
        "primary_candidate": primary,
        "secondary_diagnostic_ablations": secondary,
        "primary_increment_block_bootstrap": primary_bootstrap,
        "absolute_skill_block_bootstrap": absolute_bootstrap,
        "secondary_diagnostic_block_bootstrap": secondary_bootstrap,
        "scientific_contract": {
            "baseline": "V004 additive HGB",
            "primary": "full_financial_conditions",
            "secondary_cannot_rescue_primary": True,
            "same_v004_oos_rows": True,
            "only_market_model_changes": True,
            "same_day_vix_used": False,
            "adjusted_close_used": False,
            "SPY_QQQ_IWM_stacked": False,
            "no_post_result_tuning": True,
            "strict_historical_pit": False,
        },
    }, oos
