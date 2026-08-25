from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class PurgedMarketFold:
    fold_id: int
    first_test_day: str
    last_test_day: str
    latest_train_origin_day: str
    latest_train_target_day: str
    purged_pretest_rows: int
    train_index: tuple[int, ...]
    test_index: tuple[int, ...]


def build_purged_day_folds(
    frame: pd.DataFrame,
    *,
    n_folds: int,
    initial_fraction: float,
    min_train_days: int = 252,
    min_test_days: int = 60,
) -> list[PurgedMarketFold]:
    if not 0.20 <= initial_fraction <= 0.70:
        raise ValueError("initial_fraction outside safe range")
    required = {"origin_trading_day", "target_trading_day"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"missing split columns: {sorted(missing)}")

    work = frame.copy()
    days = np.array(
        sorted(work["origin_trading_day"].astype(str).unique()),
        dtype=str,
    )
    if len(days) < min_train_days + n_folds * min_test_days:
        raise RuntimeError(f"insufficient unique days: {len(days)}")

    first_test_pos = max(
        min_train_days,
        min(len(days) - n_folds, int(len(days) * initial_fraction)),
    )
    chunks = [
        np.asarray(x, dtype=str)
        for x in np.array_split(days[first_test_pos:], n_folds)
        if len(x)
    ]

    folds: list[PurgedMarketFold] = []
    for fold_id, chunk in enumerate(chunks):
        first_day = str(chunk[0])
        last_day = str(chunk[-1])

        test_mask = work["origin_trading_day"].astype(str).isin(set(chunk))
        # Purge any training label whose realization reaches the first test day.
        train_mask = (
            work["target_trading_day"].astype(str) < first_day
        )

        train_idx = tuple(int(i) for i in work.index[train_mask])
        test_idx = tuple(int(i) for i in work.index[test_mask])

        if not train_idx or not test_idx:
            raise RuntimeError(f"empty fold {fold_id}")

        train_days = work.loc[list(train_idx), "origin_trading_day"].nunique()
        test_days = work.loc[list(test_idx), "origin_trading_day"].nunique()
        if train_days < min_train_days or test_days < min_test_days:
            raise RuntimeError(
                f"fold {fold_id} too small: train_days={train_days}, "
                f"test_days={test_days}"
            )

        latest_train_target = max(
            work.loc[list(train_idx), "target_trading_day"].astype(str)
        )
        earliest_test_origin = min(
            work.loc[list(test_idx), "origin_trading_day"].astype(str)
        )
        if latest_train_target >= earliest_test_origin:
            raise AssertionError("target horizon leakage")

        train_origins = set(
            work.loc[list(train_idx), "origin_trading_day"].astype(str)
        )
        test_origins = set(
            work.loc[list(test_idx), "origin_trading_day"].astype(str)
        )
        if train_origins & test_origins:
            raise AssertionError("origin-day overlap")

        pretest_mask = work["origin_trading_day"].astype(str) < first_day
        purged_pretest_rows = int((pretest_mask & ~train_mask).sum())

        folds.append(
            PurgedMarketFold(
                fold_id=fold_id,
                first_test_day=first_day,
                last_test_day=last_day,
                latest_train_origin_day=str(
                    work.loc[list(train_idx), "origin_trading_day"]
                    .astype(str).max()
                ),
                latest_train_target_day=str(latest_train_target),
                purged_pretest_rows=purged_pretest_rows,
                train_index=train_idx,
                test_index=test_idx,
            )
        )
    return folds


def fold_summary(folds: list[PurgedMarketFold]) -> list[dict[str, object]]:
    return [
        {
            "fold_id": f.fold_id,
            "first_test_day": f.first_test_day,
            "last_test_day": f.last_test_day,
            "latest_train_origin_day": f.latest_train_origin_day,
            "latest_train_target_day": f.latest_train_target_day,
            "purged_pretest_rows": f.purged_pretest_rows,
            "train_rows": len(f.train_index),
            "test_rows": len(f.test_index),
        }
        for f in folds
    ]


def metrics(actual: np.ndarray, pred: np.ndarray) -> dict[str, float | int | None]:
    y = np.asarray(actual, dtype=float)
    p = np.asarray(pred, dtype=float)
    if len(y) == 0 or len(y) != len(p):
        raise ValueError("invalid metric arrays")

    err = y - p
    abs_err = np.abs(err)
    nonzero_y = y != 0

    direction = None
    if np.any(nonzero_y):
        direction = float(
            np.mean(np.sign(y[nonzero_y]) == np.sign(p[nonzero_y]))
        )

    denom = float(np.sum((y - np.mean(y)) ** 2))
    r2 = (
        None
        if denom <= 0
        else float(1.0 - np.sum(err ** 2) / denom)
    )
    return {
        "rows": int(len(y)),
        "mae_pct": float(np.mean(abs_err)),
        "median_ae_pct": float(np.median(abs_err)),
        "rmse_pct": float(np.sqrt(np.mean(err ** 2))),
        "bias_pct": float(np.mean(p - y)),
        "directional_accuracy": direction,
        "r2": r2,
    }


def daily_cross_section_diagnostics(
    frame: pd.DataFrame,
    *,
    target_col: str,
    pred_col: str,
    min_assets: int = 30,
) -> dict[str, float | int | None]:
    spearman = []
    pearson = []
    market_sign = []

    for _, g in frame.groupby("origin_trading_day", sort=True):
        y = g[target_col].to_numpy(float)
        p = g[pred_col].to_numpy(float)
        mask = np.isfinite(y) & np.isfinite(p)
        y = y[mask]
        p = p[mask]
        if len(y) < min_assets:
            continue

        y_std = float(np.std(y))
        p_std = float(np.std(p))
        if y_std > 0 and p_std > 0:
            pearson.append(float(np.corrcoef(y, p)[0, 1]))
            yr = pd.Series(y).rank(method="average").to_numpy(float)
            pr = pd.Series(p).rank(method="average").to_numpy(float)
            if np.std(yr) > 0 and np.std(pr) > 0:
                spearman.append(float(np.corrcoef(yr, pr)[0, 1]))

        actual_market = float(np.mean(y))
        pred_market = float(np.mean(p))
        if actual_market != 0:
            market_sign.append(
                float(np.sign(actual_market) == np.sign(pred_market))
            )

    def agg(values: list[float]) -> dict[str, float | int | None]:
        if not values:
            return {"days": 0, "mean": None, "median": None}
        a = np.asarray(values, dtype=float)
        return {
            "days": int(len(a)),
            "mean": float(np.mean(a)),
            "median": float(np.median(a)),
        }

    return {
        "daily_pearson_ic": agg(pearson),
        "daily_spearman_ic": agg(spearman),
        "daily_market_direction": agg(market_sign),
    }


def paired_point(
    frame: pd.DataFrame,
    *,
    baseline_col: str,
    candidate_col: str,
    target_col: str = "return_pct",
) -> dict[str, float]:
    y = frame[target_col].to_numpy(float)
    b = frame[baseline_col].to_numpy(float)
    c = frame[candidate_col].to_numpy(float)
    be = np.abs(y - b)
    ce = np.abs(y - c)
    return {
        "mae_delta_baseline_minus_candidate_pct": float(np.mean(be - ce)),
        "candidate_abs_error_win_rate": float(np.mean(ce < be)),
        "directional_accuracy_delta": float(
            np.mean(np.sign(y) == np.sign(c))
            - np.mean(np.sign(y) == np.sign(b))
        ),
    }


def moving_block_bootstrap_days(
    frame: pd.DataFrame,
    *,
    baseline_col: str,
    candidate_col: str,
    target_col: str,
    block_length: int,
    reps: int,
    seed: int,
) -> dict[str, object]:
    work = frame[
        ["origin_trading_day", target_col, baseline_col, candidate_col]
    ].copy()
    work["delta"] = (
        np.abs(work[target_col] - work[baseline_col])
        - np.abs(work[target_col] - work[candidate_col])
    )
    daily = (
        work.groupby("origin_trading_day", sort=True)["delta"]
        .mean()
        .sort_index()
    )
    values = daily.to_numpy(float)
    n = len(values)
    if n <= block_length:
        raise ValueError("insufficient days for block bootstrap")

    starts = np.arange(0, n - block_length + 1)
    rng = np.random.default_rng(seed)
    draws = []
    for _ in range(reps):
        sampled = []
        while len(sampled) < n:
            start = int(rng.choice(starts))
            sampled.extend(values[start:start + block_length])
        draws.append(float(np.mean(sampled[:n])))

    return {
        "point_delta_pct": float(np.mean(values)),
        "ci95": [
            float(np.quantile(draws, 0.025)),
            float(np.quantile(draws, 0.975)),
        ],
        "block_length_origin_days": int(block_length),
        "unique_origin_days": int(n),
        "bootstrap_reps": int(reps),
    }
