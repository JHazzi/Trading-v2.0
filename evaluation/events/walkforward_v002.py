from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class PurgedFold:
    fold_id: int
    first_test_anchor_day: str
    last_test_anchor_day: str
    train_index: tuple[int, ...]
    test_index: tuple[int, ...]


def build_purged_event_folds(
    df: pd.DataFrame,
    *,
    n_folds: int,
    initial_fraction: float,
    min_train_rows: int,
    min_test_rows: int,
) -> list[PurgedFold]:
    required = {
        "event_id",
        "event_anchor_day",
        "origin_trading_day",
        "target_trading_day",
    }
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Faltan columnas para walk-forward: {sorted(missing)}")
    if not 0.2 <= initial_fraction <= 0.8:
        raise ValueError("initial_fraction fuera de rango")
    if n_folds < 2:
        raise ValueError("n_folds debe ser >= 2")

    anchor_days = np.array(
        sorted(df["event_anchor_day"].astype(str).unique())
    )
    if len(anchor_days) < n_folds + 5:
        raise RuntimeError(
            f"Muy pocos event anchor days: {len(anchor_days)}"
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

        test_events = set(
            df.loc[
                df["event_anchor_day"].astype(str).isin(chunk),
                "event_id",
            ].astype(str)
        )
        test_mask = df["event_id"].astype(str).isin(test_events)

        # Purge any training label whose outcome reaches the test period.
        train_mask = (
            (df["target_trading_day"].astype(str) < first_day)
            & (~test_mask)
        )

        train_index = tuple(int(i) for i in df.index[train_mask])
        test_index = tuple(int(i) for i in df.index[test_mask])

        if len(train_index) < min_train_rows:
            continue
        if len(test_index) < min_test_rows:
            continue

        # Contract: one event can never occur on both sides of a fold.
        train_events = set(
            df.loc[list(train_index), "event_id"].astype(str)
        )
        if train_events & test_events:
            raise AssertionError("Event leakage entre train y test")

        # Contract: no realized training target may overlap test start.
        latest_train_target = max(
            df.loc[list(train_index), "target_trading_day"].astype(str)
        )
        if latest_train_target >= first_day:
            raise AssertionError("Target leakage por horizonte solapado")

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
            "No se pudieron construir al menos 2 folds purgados útiles"
        )
    return folds


def expanding_oof_market_predictions(
    train_df: pd.DataFrame,
    *,
    fit_predict,
    n_folds: int = 3,
    initial_fraction: float = 0.40,
    min_train_rows: int = 35,
    min_test_rows: int = 8,
) -> pd.Series:
    """
    `fit_predict(fit_frame, validation_frame, fold_id)` must return a
    one-dimensional prediction array for validation_frame.
    """
    folds = build_purged_event_folds(
        train_df,
        n_folds=n_folds,
        initial_fraction=initial_fraction,
        min_train_rows=min_train_rows,
        min_test_rows=min_test_rows,
    )
    out = pd.Series(index=train_df.index, dtype=float)

    for fold in folds:
        fit_frame = train_df.loc[list(fold.train_index)]
        val_frame = train_df.loc[list(fold.test_index)]
        pred = np.asarray(
            fit_predict(fit_frame, val_frame, fold.fold_id),
            dtype=float,
        )
        if pred.shape != (len(val_frame),):
            raise ValueError("fit_predict devolvió shape inválido")
        out.loc[val_frame.index] = pred

    return out
