from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


def metrics(y, p):
    y=np.asarray(y,float); p=np.asarray(p,float)
    e=y-p
    return {
        "rows":int(len(y)),
        "mae_pct":float(np.mean(np.abs(e))),
        "median_ae_pct":float(np.median(np.abs(e))),
        "rmse_pct":float(np.sqrt(np.mean(e**2))),
        "bias_pct":float(np.mean(p-y)),
        "directional_accuracy":float(np.mean(np.sign(y)==np.sign(p))),
    }


def paired(frame, baseline_col, candidate_col, target_col="return_pct"):
    y=frame[target_col].to_numpy(float)
    b=frame[baseline_col].to_numpy(float)
    c=frame[candidate_col].to_numpy(float)
    be=np.abs(y-b); ce=np.abs(y-c)
    return {
        "mae_delta_baseline_minus_candidate_pct":float(np.mean(be-ce)),
        "candidate_abs_error_win_rate":float(np.mean(ce<be)),
        "directional_accuracy_delta":float(
            np.mean(np.sign(y)==np.sign(c))-
            np.mean(np.sign(y)==np.sign(b))
        )
    }


def daily_ic(frame, target_col, pred_col):
    pearson=[]; spearman=[]
    for _,g in frame.groupby("origin_trading_day",sort=True):
        y=g[target_col].to_numpy(float); p=g[pred_col].to_numpy(float)
        if len(y)<30 or np.std(y)<=0 or np.std(p)<=0:
            continue
        pearson.append(float(np.corrcoef(y,p)[0,1]))
        yr=pd.Series(y).rank().to_numpy(float)
        pr=pd.Series(p).rank().to_numpy(float)
        spearman.append(float(np.corrcoef(yr,pr)[0,1]))
    def agg(x):
        return {"days":len(x),"mean":None if not x else float(np.mean(x)),
                "median":None if not x else float(np.median(x))}
    return {"daily_pearson_ic":agg(pearson),"daily_spearman_ic":agg(spearman)}


def moving_block_bootstrap_days(
    frame, baseline_col, candidate_col, target_col,
    block_length, reps, seed
):
    x=frame[["origin_trading_day",target_col,baseline_col,candidate_col]].copy()
    x["delta"]=(
        np.abs(x[target_col]-x[baseline_col])-
        np.abs(x[target_col]-x[candidate_col])
    )
    daily=x.groupby("origin_trading_day",sort=True)["delta"].mean().to_numpy(float)
    n=len(daily)
    if n<=block_length:
        raise ValueError("insufficient origin days")
    starts=np.arange(n-block_length+1)
    rng=np.random.default_rng(seed)
    draws=[]
    for _ in range(reps):
        vals=[]
        while len(vals)<n:
            j=int(rng.choice(starts))
            vals.extend(daily[j:j+block_length])
        draws.append(float(np.mean(vals[:n])))
    return {
        "point_delta_pct":float(np.mean(daily)),
        "ci95":[float(np.quantile(draws,.025)),float(np.quantile(draws,.975))],
        "unique_origin_days":int(n),
        "block_length_origin_days":int(block_length),
        "bootstrap_reps":int(reps)
    }


def load_v003_boundaries(report_path: Path):
    x=json.loads(report_path.read_text())
    return [
        {
            "fold_id":int(f["fold_id"]),
            "first_test_day":str(f["first_test_day"]),
            "last_test_day":str(f["last_test_day"])
        }
        for f in x["folds"]
    ]


def masks(frame, boundary):
    first=boundary["first_test_day"]; last=boundary["last_test_day"]
    train=frame["target_end_day"].astype(str)<first
    test=(
        (frame["origin_trading_day"].astype(str)>=first)&
        (frame["origin_trading_day"].astype(str)<=last)
    )
    if not train.any() or not test.any():
        raise RuntimeError("empty factorized fold")
    latest=str(frame.loc[train,"target_end_day"].astype(str).max())
    if latest>=first:
        raise AssertionError("factorized target leakage")
    return train,test
