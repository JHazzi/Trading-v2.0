from __future__ import annotations

from dataclasses import dataclass
import pandas as pd


@dataclass(frozen=True)
class TimeSplit:
    train: pd.DataFrame
    test: pd.DataFrame
    cutoff: pd.Timestamp


def global_time_split(
    df: pd.DataFrame,
    test_fraction: float = 0.20,
    time_column: str = "origin_time",
) -> TimeSplit:
    """Split all assets by one global market-time cutoff.

    Every observation in train is strictly earlier than the cutoff and every
    observation in test is at or after it. This prevents one asset being in
    train after another asset is already in test.
    """
    if not 0.0 < test_fraction < 1.0:
        raise ValueError("test_fraction debe estar entre 0 y 1.")

    if df.empty:
        raise ValueError("Dataset vacío.")

    out = df.copy()
    out[time_column] = pd.to_datetime(out[time_column], utc=True, errors="raise")
    out = out.sort_values([time_column, "asset_id"], kind="mergesort")

    unique_times = out[time_column].drop_duplicates().sort_values()
    if len(unique_times) < 2:
        raise ValueError("Se necesitan al menos dos timestamps distintos.")

    cutoff_idx = max(
        1,
        min(len(unique_times) - 1, int(len(unique_times) * (1.0 - test_fraction))),
    )
    cutoff = unique_times.iloc[cutoff_idx]

    train = out[out[time_column] < cutoff].copy()
    test = out[out[time_column] >= cutoff].copy()

    if train.empty or test.empty:
        raise ValueError("El split temporal produjo train/test vacío.")

    if train[time_column].max() >= test[time_column].min():
        raise AssertionError("Leakage temporal detectado.")

    return TimeSplit(train=train, test=test, cutoff=cutoff)
