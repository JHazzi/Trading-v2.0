from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

from evaluation.market.daily_v005_market_tradables_benchmark import (
    metrics,
    paired,
    daily_ic,
    moving_block_bootstrap_days,
    load_boundaries,
    masks,
)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = (
    ROOT / "config" / "market_brain_daily_v005_market_tradables_benchmark.json"
)

# Import the exact V004 loader so sector/asset state construction remains
# canonical. The V005 experiment changes only the market-level feature matrix.
from models.market.daily_v004_factorized_benchmark import (
    load_frames as load_v004_frames,
)


def load_config(path=DEFAULT_CONFIG):
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    pc = cfg["primary_contract"]
    if pc["baseline"] != "v004_additive_hgb_reconstruction":
        raise ValueError("V005 primary baseline changed")
    if pc["candidate"] != "v005_market_tradables_additive_hgb_reconstruction":
        raise ValueError("V005 primary candidate changed")
    if not pc["only_market_model_changes"]:
        raise ValueError("V005 must change only the market model")
    if not pc["same_v004_oos_rows"]:
        raise ValueError("V005 must use same V004 OOS rows")
    if cfg["causal_limitations"]["historical_reference_strict_pit"] is not False:
        raise ValueError("strict PIT must not be overclaimed")
    return cfg


def _finite(df, cols):
    return np.isfinite(df[cols].to_numpy(float)).all(axis=1)


def hgb(params):
    return HistGradientBoostingRegressor(
        loss=params["loss"],
        learning_rate=params["learning_rate"],
        max_iter=params["max_iter"],
        max_leaf_nodes=params["max_leaf_nodes"],
        min_samples_leaf=params["min_samples_leaf"],
        l2_regularization=params["l2_regularization"],
        early_stopping=params["early_stopping"],
        random_state=params["random_state"],
    )


def fit_hgb(train, test, features, target, params):
    model = hgb(params)
    model.fit(train[features], train[target])
    return model.predict(test[features])


def load_external_state(db: Path, features):
    with sqlite3.connect(db) as conn:
        x = pd.read_sql_query(
            "SELECT * FROM market_external_state_v005 ORDER BY trading_day",
            conn,
        )
    if x.empty:
        raise RuntimeError("V005 external state is empty")
    x["origin_trading_day"] = x["trading_day"].astype(str)
    if x["origin_trading_day"].duplicated().any():
        raise RuntimeError("duplicate V005 external market day")
    if int(x["point_in_time_verified"].max()) != 0:
        raise RuntimeError("historical V005 state incorrectly marked PIT")
    keep = ["origin_trading_day", *features]
    x = x[keep].copy()
    return x


def load_frames(
    math_db: Path,
    external_db: Path,
    horizon: int,
    cfg,
):
    (
        market,
        sector,
        asset,
        sector_features,
        additive_asset_features,
        dynamic_asset_features,
    ) = load_v004_frames(math_db, horizon)

    base_features = cfg["base_market_features"]
    external_features = cfg["external_market_features"]

    # Validate the V004 contract instead of silently using a changed schema.
    missing_base = [c for c in base_features if c not in market.columns]
    if missing_base:
        raise RuntimeError(f"missing V004 market features: {missing_base}")

    ext = load_external_state(external_db, external_features)
    before = len(market)
    market = market.merge(
        ext,
        on="origin_trading_day",
        how="left",
        validate="one_to_one",
    )
    if len(market) != before:
        raise RuntimeError("V005 external merge changed market row count")

    market["external_state_complete"] = _finite(
        market, external_features
    ).astype(int)

    return (
        market,
        sector,
        asset,
        sector_features,
        additive_asset_features,
        dynamic_asset_features,
    )


def load_v004_oos(path: Path):
    x = pd.read_csv(path)
    required = [
        "state_id",
        "pred_train_median",
        "pred_hgb_full",
        "pred_additive_hgb",
    ]
    missing = [c for c in required if c not in x.columns]
    if missing:
        raise RuntimeError(f"V004 OOS missing columns: {missing}")
    return x[required].rename(
        columns={"pred_additive_hgb": "pred_v004_additive_hgb"}
    )


def run_horizon(
    math_db: Path,
    external_db: Path,
    v004_dir: Path,
    horizon: int,
    cfg,
):
    (
        market,
        sector,
        asset,
        sector_features,
        additive_asset_features,
        _,
    ) = load_frames(math_db, external_db, horizon, cfg)

    boundaries = load_boundaries(
        v004_dir / f"h{horizon}_factorized_benchmark.json"
    )
    old_v004 = load_v004_oos(
        v004_dir / f"h{horizon}_factorized_oos.csv.gz"
    )

    base_features = cfg["base_market_features"]
    ext_features = cfg["external_market_features"]
    enriched_features = [*base_features, *ext_features]

    parts = []
    component_folds = []
    for b in boundaries:
        mtr, mte = masks(market, b)
        str_, ste = masks(sector, b)
        atr, ate = masks(asset, b)

        mt = market[mtr].copy()
        mv = market[mte].copy()
        st = sector[str_].copy()
        sv = sector[ste].copy()
        at = asset[atr].copy()
        av = asset[ate].copy()

        # Coverage is part of the preregistered design, not something to
        # silently impute or filter after seeing results.
        if not bool(mt["external_state_complete"].all()):
            raise RuntimeError(
                f"missing V005 external state in training fold {b['fold_id']}"
            )
        if not bool(mv["external_state_complete"].all()):
            raise RuntimeError(
                f"missing V005 external state in test fold {b['fold_id']}"
            )

        # Base market fit is rerun only as an implementation-parity control.
        mv["pred_market_v004_replay"] = fit_hgb(
            mt, mv, base_features, "target_market",
            cfg["models"]["market_hgb"],
        )
        mv["pred_market_v005"] = fit_hgb(
            mt, mv, enriched_features, "target_market",
            cfg["models"]["market_hgb"],
        )

        # Sector and asset are identical to V004. They are fit ONCE per fold
        # and reused for both reconstructions.
        sv["pred_sector_shared"] = fit_hgb(
            st, sv, sector_features, "target_sector",
            cfg["models"]["sector_hgb"],
        )
        av["pred_asset_shared"] = fit_hgb(
            at,
            av,
            additive_asset_features,
            "target_asset_additive_residual_pct",
            cfg["models"]["asset_hgb"],
        )

        av = av.merge(
            mv[
                [
                    "origin_trading_day",
                    "pred_market_v004_replay",
                    "pred_market_v005",
                ]
            ],
            on="origin_trading_day",
            how="inner",
            validate="many_to_one",
        )
        av = av.merge(
            sv[["origin_trading_day", "sector", "pred_sector_shared"]],
            on=["origin_trading_day", "sector"],
            how="inner",
            validate="many_to_one",
        )
        # Stored V004 OOS is the single frozen reference. It already carries
        # train-median and V003-HGB predictions, so do not merge V003 OOS again.
        av = av.merge(
            old_v004,
            on="state_id",
            how="inner",
            validate="one_to_one",
        )

        av["pred_v004_replay"] = (
            av["pred_market_v004_replay"]
            + av["pred_sector_shared"]
            + av["pred_asset_shared"]
        )
        av["pred_v005_enriched"] = (
            av["pred_market_v005"]
            + av["pred_sector_shared"]
            + av["pred_asset_shared"]
        )
        av["fold_id"] = int(b["fold_id"])

        # The exact V004 algorithm should replay stored V004 predictions.
        max_abs_parity = float(
            np.max(
                np.abs(
                    av["pred_v004_replay"].to_numpy(float)
                    - av["pred_v004_additive_hgb"].to_numpy(float)
                )
            )
        )
        if max_abs_parity > 1e-9:
            raise AssertionError(
                f"V004 replay parity failure fold={b['fold_id']} "
                f"max_abs={max_abs_parity}"
            )

        component_folds.append(
            {
                "fold_id": int(b["fold_id"]),
                "first_test_day": b["first_test_day"],
                "last_test_day": b["last_test_day"],
                "market_v004_replay": metrics(
                    mv["target_market"], mv["pred_market_v004_replay"]
                ),
                "market_v005_enriched": metrics(
                    mv["target_market"], mv["pred_market_v005"]
                ),
                "market_increment": paired(
                    mv.rename(
                        columns={
                            "target_market": "return_pct",
                            "pred_market_v004_replay": "base",
                            "pred_market_v005": "cand",
                        }
                    ),
                    "base",
                    "cand",
                ),
                "sector_shared": metrics(
                    sv["target_sector"], sv["pred_sector_shared"]
                ),
                "asset_shared": metrics(
                    av["target_asset_additive_residual_pct"],
                    av["pred_asset_shared"],
                ),
                "v004_replay_max_abs_prediction_error": max_abs_parity,
            }
        )
        parts.append(av)

    oos = pd.concat(parts, ignore_index=True)

    # Same-row hard gate.
    if len(oos) != len(old_v004):
        raise AssertionError(
            f"V005 OOS rows {len(oos)} != stored V004 OOS rows {len(old_v004)}"
        )
    if set(oos["state_id"].astype(str)) != set(old_v004["state_id"].astype(str)):
        raise AssertionError("V005 OOS state set differs from V004")

    primary = paired(
        oos,
        "pred_v004_additive_hgb",
        "pred_v005_enriched",
    )
    absolute_vs_median = paired(
        oos,
        "pred_train_median",
        "pred_v005_enriched",
    )
    vs_v003 = paired(
        oos,
        "pred_hgb_full",
        "pred_v005_enriched",
    )

    inc_boot = {}
    abs_boot = {}
    for L in cfg["bootstrap"]["moving_block_lengths_origin_days"]:
        inc_boot[str(L)] = moving_block_bootstrap_days(
            oos,
            "pred_v004_additive_hgb",
            "pred_v005_enriched",
            "return_pct",
            L,
            cfg["bootstrap"]["reps"],
            cfg["bootstrap"]["seed"] + 1000 * horizon + L,
        )
        abs_boot[str(L)] = moving_block_bootstrap_days(
            oos,
            "pred_train_median",
            "pred_v005_enriched",
            "return_pct",
            L,
            cfg["bootstrap"]["reps"],
            cfg["bootstrap"]["seed"] + 2000 * horizon + L,
        )

    result = {
        "benchmark_version": cfg["version"],
        "horizon_sessions": int(horizon),
        "oos_rows": int(len(oos)),
        "oos_assets": int(oos["asset_id"].nunique()),
        "oos_origin_days": int(oos["origin_trading_day"].nunique()),
        "feature_contract": {
            "base_market_features": len(base_features),
            "external_market_features": len(ext_features),
            "enriched_market_features": len(enriched_features),
            "sector_model_changed": False,
            "asset_model_changed": False,
        },
        "component_fold_metrics": component_folds,
        "candidate": {
            "metrics": metrics(oos["return_pct"], oos["pred_v005_enriched"]),
            "increment_vs_v004": primary,
            "absolute_vs_train_median": absolute_vs_median,
            "vs_v003_hgb_full": vs_v003,
            "cross_section": daily_ic(
                oos, "return_pct", "pred_v005_enriched"
            ),
        },
        "primary_increment_block_bootstrap": inc_boot,
        "absolute_skill_block_bootstrap": abs_boot,
        "scientific_contract": {
            "primary_comparison": "V004 additive HGB vs V005 enriched HGB",
            "positive_delta_means_v005_better": True,
            "same_v004_oos_rows": True,
            "only_market_model_changed": True,
            "no_hyperparameter_tuning": True,
            "historical_reference_strict_pit": False,
        },
    }
    return result, oos
