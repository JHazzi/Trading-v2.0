from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from evaluation.market.daily_v004_factorized_benchmark import (
    metrics, paired, daily_ic, moving_block_bootstrap_days,
    load_v003_boundaries, masks,
)

ROOT=Path(__file__).resolve().parents[2]
DEFAULT_CONFIG=ROOT/"config"/"market_brain_daily_v004_factorized_benchmark.json"

MARKET_FEATURES=[
    "market_return_1d_pct","market_return_5d_pct","market_return_20d_pct",
    "market_breadth_positive_1d","market_breadth_positive_5d",
    "market_dispersion_1d_pct","market_mean_vol_20d_pct",
    "market_realized_vol_20d_pct","market_realized_vol_63d_pct",
    "market_trend_20d_pct","market_trend_63d_pct",
    "market_drawdown_63d_pct","market_drawdown_252d_pct",
]
SECTOR_FEATURES=[
    "sector_return_1d_pct","sector_return_5d_pct","sector_return_20d_pct",
    "sector_breadth_positive_1d","sector_mean_vol_20d_pct",
    "sector_minus_market_1d_pct","sector_minus_market_5d_pct",
    "sector_minus_market_20d_pct",
]
ASSET_EXCLUDE={
    "state_id","asset_math_state_id","asset_id","ticker","sector","trading_day",
}


def load_config(path=DEFAULT_CONFIG):
    cfg=json.loads(Path(path).read_text())
    if cfg["primary_candidate"]!="hgb_additive_reconstruction":
        raise ValueError("primary changed")
    if cfg["primary_baseline"]!="v003_fold_train_median":
        raise ValueError("baseline changed")
    if not cfg["same_outer_fold_boundaries_as_v003"]:
        raise ValueError("V004 must reuse V003 boundaries")
    return cfg


def _finite(df, cols):
    return np.isfinite(df[cols].to_numpy(float)).all(axis=1)


def load_frames(db: Path, horizon: int):
    with sqlite3.connect(db) as c:
        market=pd.read_sql_query("SELECT * FROM v004_market_states ORDER BY trading_day",c)
        sector=pd.read_sql_query("SELECT * FROM v004_sector_states ORDER BY trading_day,sector",c)
        asset=pd.read_sql_query("SELECT * FROM v004_asset_states ORDER BY trading_day,asset_id",c)
        target=pd.read_sql_query(
            "SELECT * FROM v004_factor_targets WHERE horizon_sessions=? "
            "ORDER BY origin_trading_day,asset_id",c,params=(int(horizon),)
        )

    # One future market target per day, with target_end_day chosen conservatively
    # as the latest constituent target day.
    mt=(
        target.groupby("origin_trading_day",sort=True)
        .agg(
            target_market=("future_market_return_pct","first"),
            target_end_day=("target_trading_day","max"),
        ).reset_index()
    )
    market=market.rename(columns={"trading_day":"origin_trading_day"}).merge(
        mt,on="origin_trading_day",how="inner",validate="one_to_one"
    )
    market=market[_finite(market,MARKET_FEATURES)].copy()

    st=(
        target.groupby(["origin_trading_day","sector"],sort=True)
        .agg(
            target_sector=("target_sector_additive_pct","first"),
            target_end_day=("target_trading_day","max"),
        ).reset_index()
    )
    sector=sector.rename(columns={"trading_day":"origin_trading_day"}).merge(
        st,on=["origin_trading_day","sector"],how="inner",validate="one_to_one"
    )
    # Current market regime can legitimately condition a sector residual.
    market_ctx=market[["origin_trading_day",*MARKET_FEATURES]].copy()
    sector=sector.merge(market_ctx,on="origin_trading_day",how="inner",
                        suffixes=("","_market"),validate="many_to_one")
    sec_features=SECTOR_FEATURES+[f"{x}_market" for x in MARKET_FEATURES if x in sector.columns and f"{x}_market" in sector.columns]
    # Overlapping names are suffixed only when present in both frames. Add the
    # unsuffixed market-only features as well.
    for x in MARKET_FEATURES:
        if x in sector.columns and x not in SECTOR_FEATURES and x not in sec_features:
            sec_features.append(x)
    sector=sector[_finite(sector,sec_features)].copy()

    asset=asset.rename(columns={"trading_day":"origin_trading_day"})
    asset=target.merge(
        asset,on=["state_id","asset_id","sector","origin_trading_day"],
        how="inner",validate="many_to_one",suffixes=("","_state")
    )
    numeric=[
        c for c in asset.columns
        if c not in ASSET_EXCLUDE
        and c not in {
            "origin_trading_day","target_trading_day","label_status",
            "horizon_sessions","feature_version","state_time"
        }
        and pd.api.types.is_numeric_dtype(asset[c])
    ]
    prohibited={
        "return_pct","future_market_return_pct","future_sector_return_pct",
        "target_market_additive_pct","target_sector_additive_pct",
        "target_asset_additive_residual_pct",
        "target_market_beta_component_pct","target_sector_beta_component_pct",
        "target_asset_beta_residual_pct","additive_identity_error",
        "beta_identity_error","dynamic_factorization_ready"
    }
    asset_features=[c for c in numeric if c not in prohibited]
    asset=asset[_finite(asset,asset_features)].copy()
    asset["target_end_day"]=asset["target_trading_day"].astype(str)

    return market,sector,asset,sec_features,asset_features


def hgb(params,seed):
    return HistGradientBoostingRegressor(
        loss=params["loss"],learning_rate=params["learning_rate"],
        max_iter=params["max_iter"],max_leaf_nodes=params["max_leaf_nodes"],
        min_samples_leaf=params["min_samples_leaf"],
        l2_regularization=params["l2_regularization"],
        early_stopping=params["early_stopping"],random_state=seed,
    )


def ridge(params):
    return Pipeline([
        ("scale",StandardScaler()),
        ("model",Ridge(alpha=params["alpha"],solver=params["solver"]))
    ])


def fit_level(train,test,features,target,cfg,model_key,family):
    if family=="hgb":
        model=hgb(cfg["models"][model_key],42)
    else:
        model=ridge(cfg["models"]["ridge"])
    model.fit(train[features],train[target])
    return model.predict(test[features])


def v003_oos(path: Path):
    x=pd.read_csv(path)
    keep=["state_id","pred_train_median","pred_hgb_full"]
    return x[keep].copy()


def run_horizon(db:Path,v003_dir:Path,horizon:int,cfg):
    market,sector,asset,sec_features,asset_features=load_frames(db,horizon)
    boundaries=load_v003_boundaries(v003_dir/f"h{horizon}_benchmark.json")
    old=v003_oos(v003_dir/f"h{horizon}_oos.csv.gz")

    parts=[]; component=[]
    for b in boundaries:
        mtr,mte=masks(market,b); str_,ste=masks(sector,b); atr,ate=masks(asset,b)

        mt=market[mtr]; mv=market[mte].copy()
        st=sector[str_]; sv=sector[ste].copy()
        at=asset[atr]; av=asset[ate].copy()

        for fam in ("hgb","ridge"):
            mv[f"pred_market_{fam}"]=fit_level(
                mt,mv,MARKET_FEATURES,"target_market",cfg,"market_hgb",fam
            )
            sv[f"pred_sector_{fam}"]=fit_level(
                st,sv,sec_features,"target_sector",cfg,"sector_hgb",fam
            )
            av[f"pred_asset_add_{fam}"]=fit_level(
                at,av,asset_features,"target_asset_additive_residual_pct",
                cfg,"asset_hgb",fam
            )

            dyn_train=at[at["dynamic_factorization_ready"]==1]
            dyn_test=av[av["dynamic_factorization_ready"]==1].copy()
            if len(dyn_train) and len(dyn_test):
                dyn_test[f"pred_asset_dyn_{fam}"]=fit_level(
                    dyn_train,dyn_test,asset_features,
                    "target_asset_beta_residual_pct",cfg,"asset_hgb",fam
                )
                av=av.merge(
                    dyn_test[["state_id",f"pred_asset_dyn_{fam}"]],
                    on="state_id",how="left",validate="one_to_one"
                )

        # Attach component predictions at their natural units.
        mp=mv[["origin_trading_day","pred_market_hgb","pred_market_ridge"]]
        sp=sv[["origin_trading_day","sector","pred_sector_hgb","pred_sector_ridge"]]
        av=av.merge(mp,on="origin_trading_day",how="inner",validate="many_to_one")
        av=av.merge(sp,on=["origin_trading_day","sector"],how="inner",
                    validate="many_to_one")
        av=av.merge(old,on="state_id",how="inner",validate="one_to_one")

        for fam in ("hgb","ridge"):
            av[f"pred_additive_{fam}"]=(
                av[f"pred_market_{fam}"]+
                av[f"pred_sector_{fam}"]+
                av[f"pred_asset_add_{fam}"]
            )
            av[f"pred_dynamic_{fam}"]=(
                av["beta_market_252"]*av[f"pred_market_{fam}"]+
                av["gamma_sector_252"]*av[f"pred_sector_{fam}"]+
                av[f"pred_asset_dyn_{fam}"]
            )

        av["fold_id"]=b["fold_id"]
        parts.append(av)

        component.append({
            "fold_id":b["fold_id"],
            "first_test_day":b["first_test_day"],
            "last_test_day":b["last_test_day"],
            "market_hgb":metrics(mv.target_market,mv.pred_market_hgb),
            "sector_hgb":metrics(sv.target_sector,sv.pred_sector_hgb),
            "asset_add_hgb":metrics(av.target_asset_additive_residual_pct,
                                    av.pred_asset_add_hgb),
        })

    oos=pd.concat(parts,ignore_index=True)

    candidates={}
    for c in ("pred_additive_hgb","pred_additive_ridge"):
        candidates[c]={
            "metrics":metrics(oos.return_pct,oos[c]),
            "vs_train_median":paired(oos,"pred_train_median",c),
            "vs_v003_hgb_full":paired(oos,"pred_hgb_full",c),
            "cross_section":daily_ic(oos,"return_pct",c),
        }

    dyn=oos.dropna(subset=["pred_dynamic_hgb","pred_dynamic_ridge"]).copy()
    for c in ("pred_dynamic_hgb","pred_dynamic_ridge"):
        candidates[c]={
            "rows":int(len(dyn)),
            "metrics":metrics(dyn.return_pct,dyn[c]),
            "vs_train_median":paired(dyn,"pred_train_median",c),
            "vs_v003_hgb_full":paired(dyn,"pred_hgb_full",c),
            "cross_section":daily_ic(dyn,"return_pct",c),
        }

    boot={}
    for L in cfg["bootstrap"]["moving_block_lengths_origin_days"]:
        boot[str(L)]=moving_block_bootstrap_days(
            oos,"pred_train_median","pred_additive_hgb","return_pct",
            L,cfg["bootstrap"]["reps"],cfg["bootstrap"]["seed"]+100*horizon+L
        )

    result={
        "benchmark_version":cfg["version"],
        "horizon_sessions":horizon,
        "oos_rows":int(len(oos)),
        "oos_assets":int(oos.asset_id.nunique()),
        "oos_origin_days":int(oos.origin_trading_day.nunique()),
        "component_fold_metrics":component,
        "candidates":candidates,
        "primary_block_bootstrap":boot,
        "primary_contract":{
            "candidate":"pred_additive_hgb",
            "baseline":"pred_train_median",
            "same_v003_test_rows_required":True,
            "dynamic_beta_secondary":True,
            "no_post_result_tuning":True,
        }
    }
    return result,oos
