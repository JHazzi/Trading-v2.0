from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from evaluation.events.walkforward_v002 import PurgedFold
from models.events.dataset_v002 import TARGET

ROBUSTNESS_VERSION = "event_brain_v0021_robustness"
PRIMARY_HORIZON = 10
PRIMARY_BLOCK_LENGTH = 10
BLOCK_LENGTHS = (5, 10, 20)
RF_SEEDS = (7, 17, 42, 123, 2026)
SIMPLE_FAMILIES = ("ridge", "elasticnet", "huber")
PRIMARY_INITIAL_FRACTION = 0.45
EARLY_OOS_INITIAL_FRACTION = 0.30
OUTER_FOLDS = 4
INNER_FOLDS = 3
BOOTSTRAP_REPS = 5000


def _chunks(values: list[str], size: int = 400) -> Iterable[list[str]]:
    for start in range(0, len(values), size):
        yield values[start:start + size]


def parse_sec_accession_identity(identity_key: str) -> str:
    parts = str(identity_key).split(":", 2)
    if len(parts) != 3 or parts[0] != "sec" or not parts[1]:
        raise ValueError(f"SEC identity_key inesperado: {identity_key!r}")
    return parts[1]


def attach_accession_numbers(db: Path, frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        raise ValueError("Dataset vacío")
    event_ids = sorted(frame["event_id"].astype(str).unique())
    mapping: dict[str, str] = {}
    methods: dict[str, str] = {}

    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        for chunk in _chunks(event_ids):
            placeholders = ",".join("?" for _ in chunk)
            rows = conn.execute(
                f"""
                SELECT event_id, identity_method, identity_key
                FROM normalized_event_identities
                WHERE event_id IN ({placeholders})
                """,
                chunk,
            ).fetchall()
            for row in rows:
                event_id = str(row["event_id"])
                method = str(row["identity_method"])
                methods[event_id] = method
                if method != "sec_accession_item_v001":
                    continue
                mapping[event_id] = parse_sec_accession_identity(
                    str(row["identity_key"])
                )

    missing = sorted(set(event_ids) - set(mapping))
    wrong_methods = {
        event_id: methods.get(event_id)
        for event_id in missing
        if event_id in methods
    }
    if missing:
        raise RuntimeError(
            "No se pudo resolver accession para todos los eventos V003.1: "
            + json.dumps(
                {
                    "missing_count": len(missing),
                    "examples": missing[:10],
                    "non_sec_methods": wrong_methods,
                },
                sort_keys=True,
            )
        )

    out = frame.copy()
    out["accession_number"] = out["event_id"].astype(str).map(mapping)
    if out["accession_number"].isna().any():
        raise AssertionError("accession_number quedó NULL")
    return out


def dependence_audit(frame: pd.DataFrame) -> dict[str, object]:
    required = {
        "event_id",
        "accession_number",
        "asset_id",
        "event_type",
        "origin_trading_day",
        "target_trading_day",
        "event_anchor_day",
    }
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Faltan columnas: {sorted(missing)}")

    accession_rows = frame.groupby("accession_number").size()
    accession_events = frame.groupby("accession_number")["event_id"].nunique()
    origin_rows = frame.groupby("origin_trading_day").size()

    multi_event_accessions = accession_events[accession_events > 1]
    rows_in_multi_event_accessions = int(
        frame[
            frame["accession_number"].isin(multi_event_accessions.index)
        ].shape[0]
    )

    return {
        "rows": int(len(frame)),
        "unique_events": int(frame["event_id"].nunique()),
        "unique_accessions": int(frame["accession_number"].nunique()),
        "unique_origin_days": int(frame["origin_trading_day"].nunique()),
        "assets": int(frame["asset_id"].nunique()),
        "event_types": int(frame["event_type"].nunique()),
        "repeated_event_ids": int(
            (frame.groupby("event_id").size() > 1).sum()
        ),
        "accessions_with_multiple_events": int(len(multi_event_accessions)),
        "rows_in_multi_event_accessions": rows_in_multi_event_accessions,
        "rows_in_multi_event_accessions_fraction": float(
            rows_in_multi_event_accessions / len(frame)
        ),
        "max_rows_per_accession": int(accession_rows.max()),
        "median_rows_per_accession": float(accession_rows.median()),
        "max_events_per_accession": int(accession_events.max()),
        "median_events_per_accession": float(accession_events.median()),
        "max_rows_per_origin_day": int(origin_rows.max()),
        "median_rows_per_origin_day": float(origin_rows.median()),
    }


def build_purged_group_folds(
    df: pd.DataFrame,
    *,
    group_column: str,
    n_folds: int,
    initial_fraction: float,
    min_train_rows: int,
    min_test_rows: int,
) -> list[PurgedFold]:
    required = {
        group_column,
        "event_id",
        "event_anchor_day",
        "target_trading_day",
    }
    missing = required - set(df.columns)
    if missing:
        raise ValueError(
            f"Faltan columnas para folds agrupados: {sorted(missing)}"
        )
    if not 0.2 <= initial_fraction <= 0.8:
        raise ValueError("initial_fraction fuera de rango")
    if n_folds < 2:
        raise ValueError("n_folds debe ser >=2")

    work = df.copy()
    work["_group"] = work[group_column].astype(str)
    group_anchor = (
        work.groupby("_group")["event_anchor_day"]
        .min()
        .astype(str)
    )
    anchor_days = np.array(sorted(group_anchor.unique()), dtype=str)
    if len(anchor_days) < n_folds + 5:
        raise RuntimeError(
            f"Muy pocos anchor days de grupo: {len(anchor_days)}"
        )

    first_test_pos = max(
        1,
        min(
            len(anchor_days) - n_folds,
            int(len(anchor_days) * initial_fraction),
        ),
    )
    remaining = anchor_days[first_test_pos:]
    chunks = [
        np.asarray(chunk, dtype=str)
        for chunk in np.array_split(remaining, n_folds)
        if len(chunk)
    ]

    folds: list[PurgedFold] = []
    for fold_id, chunk in enumerate(chunks):
        first_day = str(chunk[0])
        last_day = str(chunk[-1])
        test_groups = set(group_anchor[group_anchor.isin(chunk)].index)
        test_mask = work["_group"].isin(test_groups)
        train_mask = (
            (work["target_trading_day"].astype(str) < first_day)
            & (~test_mask)
        )

        train_index = tuple(int(i) for i in work.index[train_mask])
        test_index = tuple(int(i) for i in work.index[test_mask])
        if len(train_index) < min_train_rows or len(test_index) < min_test_rows:
            continue

        train_groups = set(work.loc[list(train_index), "_group"])
        if train_groups & test_groups:
            raise AssertionError(
                f"{group_column} leakage entre train/test"
            )

        train_events = set(
            work.loc[list(train_index), "event_id"].astype(str)
        )
        test_events = set(
            work.loc[list(test_index), "event_id"].astype(str)
        )
        if train_events & test_events:
            raise AssertionError("Event leakage entre train/test")

        latest_train_target = max(
            work.loc[list(train_index), "target_trading_day"].astype(str)
        )
        if latest_train_target >= first_day:
            raise AssertionError("Target leakage por horizonte")

        folds.append(
            PurgedFold(
                fold_id=fold_id,
                first_test_anchor_day=first_day,
                last_test_anchor_day=last_day,
                train_index=train_index,
                test_index=test_index,
            )
        )

    if len(folds) < 2:
        raise RuntimeError(
            "No se pudieron construir al menos 2 folds agrupados útiles"
        )
    return folds


def fold_contract(folds: list[PurgedFold]) -> list[dict[str, object]]:
    return [
        {
            **asdict(fold),
            "train_index": len(fold.train_index),
            "test_index": len(fold.test_index),
        }
        for fold in folds
    ]


def add_fold_point_baselines(
    oos_parts: list[pd.DataFrame],
    train_frames: list[pd.DataFrame],
) -> pd.DataFrame:
    if len(oos_parts) != len(train_frames):
        raise ValueError("oos_parts/train_frames mismatch")

    out_parts: list[pd.DataFrame] = []
    for test, train in zip(oos_parts, train_frames):
        part = test.copy()
        y_train = train[TARGET].to_numpy(float)
        part["pred_train_mean"] = float(np.mean(y_train))
        part["pred_train_median"] = float(np.median(y_train))
        out_parts.append(part)
    return pd.concat(out_parts, ignore_index=False)


def directional_baselines_by_fold(
    oos: pd.DataFrame,
    train_by_fold: dict[int, pd.DataFrame],
) -> dict[str, object]:
    pieces = []
    for fold_id, test in oos.groupby("fold_id"):
        train = train_by_fold[int(fold_id)]
        train_y = train[TARGET].to_numpy(float)
        positives = int(np.sum(train_y > 0))
        negatives = int(np.sum(train_y < 0))
        majority = 1 if positives >= negatives else -1

        actual = np.sign(test[TARGET].to_numpy(float))
        actual_nonzero = actual != 0
        if not np.any(actual_nonzero):
            continue
        actual = actual[actual_nonzero]
        pieces.append(
            {
                "fold_id": int(fold_id),
                "rows": int(len(actual)),
                "train_majority_sign": int(majority),
                "always_up_accuracy": float(np.mean(actual == 1)),
                "always_down_accuracy": float(np.mean(actual == -1)),
                "train_majority_accuracy": float(
                    np.mean(actual == majority)
                ),
            }
        )

    weights = np.array([p["rows"] for p in pieces], dtype=float)
    def weighted(name: str) -> float:
        vals = np.array([p[name] for p in pieces], dtype=float)
        return float(np.average(vals, weights=weights))

    return {
        "folds": pieces,
        "pooled_weighted": {
            "always_up_accuracy": weighted("always_up_accuracy"),
            "always_down_accuracy": weighted("always_down_accuracy"),
            "train_majority_accuracy": weighted("train_majority_accuracy"),
        },
    }


def paired_point(
    frame: pd.DataFrame,
    *,
    baseline_col: str,
    candidate_col: str,
) -> dict[str, float]:
    y = frame[TARGET].to_numpy(float)
    base = frame[baseline_col].to_numpy(float)
    cand = frame[candidate_col].to_numpy(float)
    base_abs = np.abs(y - base)
    cand_abs = np.abs(y - cand)
    return {
        "mae_delta_baseline_minus_candidate_pct": float(
            np.mean(base_abs - cand_abs)
        ),
        "candidate_abs_error_win_rate": float(
            np.mean(cand_abs < base_abs)
        ),
        "directional_accuracy_delta": float(
            np.mean(np.sign(y) == np.sign(cand))
            - np.mean(np.sign(y) == np.sign(base))
        ),
    }


def _ci(values: list[float]) -> list[float]:
    return [
        float(np.quantile(values, 0.025)),
        float(np.quantile(values, 0.975)),
    ]


def accession_cluster_bootstrap(
    frame: pd.DataFrame,
    *,
    baseline_col: str,
    candidate_col: str,
    reps: int,
    seed: int,
) -> dict[str, object]:
    point = paired_point(
        frame,
        baseline_col=baseline_col,
        candidate_col=candidate_col,
    )
    groups = {
        str(group): np.asarray(index, dtype=int)
        for group, index in frame.groupby("accession_number").indices.items()
    }
    names = np.array(sorted(groups), dtype=object)
    rng = np.random.default_rng(seed)
    y = frame[TARGET].to_numpy(float)
    base = frame[baseline_col].to_numpy(float)
    cand = frame[candidate_col].to_numpy(float)
    deltas: list[float] = []
    wins: list[float] = []
    dir_deltas: list[float] = []

    for _ in range(reps):
        sampled = rng.choice(names, size=len(names), replace=True)
        idx = np.concatenate([groups[str(name)] for name in sampled])
        sy, sb, sc = y[idx], base[idx], cand[idx]
        sb_abs = np.abs(sy - sb)
        sc_abs = np.abs(sy - sc)
        deltas.append(float(np.mean(sb_abs - sc_abs)))
        wins.append(float(np.mean(sc_abs < sb_abs)))
        dir_deltas.append(
            float(
                np.mean(np.sign(sy) == np.sign(sc))
                - np.mean(np.sign(sy) == np.sign(sb))
            )
        )

    return {
        **point,
        "mae_delta_ci95": _ci(deltas),
        "candidate_abs_error_win_rate_ci95": _ci(wins),
        "directional_accuracy_delta_ci95": _ci(dir_deltas),
        "bootstrap_unit": "sec_accession_cluster",
        "unique_accessions": int(len(names)),
        "bootstrap_reps": int(reps),
    }


def moving_block_bootstrap(
    frame: pd.DataFrame,
    *,
    baseline_col: str,
    candidate_col: str,
    block_length: int,
    reps: int,
    seed: int,
) -> dict[str, object]:
    if block_length < 2:
        raise ValueError("block_length debe ser >=2")

    point = paired_point(
        frame,
        baseline_col=baseline_col,
        candidate_col=candidate_col,
    )
    grouped = {
        str(day): np.asarray(index, dtype=int)
        for day, index in frame.groupby("origin_trading_day").indices.items()
    }
    days = np.array(sorted(grouped), dtype=object)
    n_days = len(days)
    if n_days <= block_length:
        raise ValueError("Muy pocos origin days para moving-block bootstrap")

    starts = np.arange(0, n_days - block_length + 1)
    rng = np.random.default_rng(seed)
    y = frame[TARGET].to_numpy(float)
    base = frame[baseline_col].to_numpy(float)
    cand = frame[candidate_col].to_numpy(float)

    deltas: list[float] = []
    wins: list[float] = []
    dir_deltas: list[float] = []
    for _ in range(reps):
        sampled_days: list[str] = []
        while len(sampled_days) < n_days:
            start = int(rng.choice(starts))
            sampled_days.extend(
                str(day)
                for day in days[start:start + block_length]
            )
        sampled_days = sampled_days[:n_days]
        idx = np.concatenate([grouped[day] for day in sampled_days])
        sy, sb, sc = y[idx], base[idx], cand[idx]
        sb_abs = np.abs(sy - sb)
        sc_abs = np.abs(sy - sc)
        deltas.append(float(np.mean(sb_abs - sc_abs)))
        wins.append(float(np.mean(sc_abs < sb_abs)))
        dir_deltas.append(
            float(
                np.mean(np.sign(sy) == np.sign(sc))
                - np.mean(np.sign(sy) == np.sign(sb))
            )
        )

    return {
        **point,
        "mae_delta_ci95": _ci(deltas),
        "candidate_abs_error_win_rate_ci95": _ci(wins),
        "directional_accuracy_delta_ci95": _ci(dir_deltas),
        "bootstrap_unit": "moving_block_origin_days",
        "block_length_origin_days": int(block_length),
        "unique_origin_days": int(n_days),
        "bootstrap_reps": int(reps),
    }


def day_delta_autocorrelation(
    frame: pd.DataFrame,
    *,
    baseline_col: str,
    candidate_col: str,
    max_lag: int = 10,
) -> dict[str, float | None]:
    work = frame[
        ["origin_trading_day", TARGET, baseline_col, candidate_col]
    ].copy()
    work["paired_abs_error_delta"] = (
        np.abs(work[TARGET] - work[baseline_col])
        - np.abs(work[TARGET] - work[candidate_col])
    )
    series = (
        work.groupby("origin_trading_day")["paired_abs_error_delta"]
        .mean()
        .sort_index()
    )
    out: dict[str, float | None] = {}
    for lag in range(1, max_lag + 1):
        value = series.autocorr(lag=lag)
        out[str(lag)] = (
            None if value is None or not np.isfinite(value)
            else float(value)
        )
    return out


def leave_one_group_out_sensitivity(
    frame: pd.DataFrame,
    *,
    group_column: str,
    baseline_col: str,
    candidate_col: str,
    min_group_rows: int = 15,
) -> dict[str, object]:
    counts = frame.groupby(group_column).size()
    result: dict[str, object] = {}
    for group, count in counts.items():
        if int(count) < min_group_rows:
            continue
        subset = frame[frame[group_column] != group]
        point = paired_point(
            subset,
            baseline_col=baseline_col,
            candidate_col=candidate_col,
        )
        result[str(group)] = {
            "removed_rows": int(count),
            "remaining_rows": int(len(subset)),
            **point,
        }
    return result


def extreme_outcome_sensitivity(
    frame: pd.DataFrame,
    *,
    baseline_col: str,
    candidate_col: str,
    keep_quantiles: tuple[float, ...] = (0.975, 0.99),
) -> dict[str, object]:
    abs_target = np.abs(frame[TARGET].to_numpy(float))
    out: dict[str, object] = {}
    for q in keep_quantiles:
        threshold = float(np.quantile(abs_target, q))
        subset = frame[np.abs(frame[TARGET]) <= threshold]
        out[f"keep_abs_target_le_q{q:.3f}"] = {
            "quantile": float(q),
            "abs_target_threshold_pct": threshold,
            "rows": int(len(subset)),
            **paired_point(
                subset,
                baseline_col=baseline_col,
                candidate_col=candidate_col,
            ),
        }
    return out
