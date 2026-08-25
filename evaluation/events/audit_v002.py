from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from models.events.dataset_v002 import load_dataset


@dataclass(frozen=True)
class DatasetGates:
    min_rows: int = 200
    min_assets: int = 8
    min_event_types: int = 5
    min_unique_events: int = 180
    min_span_days: int = 365
    max_asset_share: float = 0.30
    max_event_type_share: float = 0.45


def audit_frame(
    df: pd.DataFrame,
    *,
    gates: DatasetGates = DatasetGates(),
) -> dict[str, object]:
    if df.empty:
        return {
            "status": "FAIL",
            "failures": ["dataset_empty"],
            "rows": 0,
        }

    rows = len(df)
    assets = int(df["asset_id"].nunique())
    event_types = int(df["event_type"].nunique())
    unique_events = int(df["event_id"].nunique())
    first_day = pd.Timestamp(df["origin_trading_day"].min())
    last_day = pd.Timestamp(df["origin_trading_day"].max())
    span_days = int((last_day - first_day).days)

    asset_counts = df.groupby("asset_id").size().sort_values(ascending=False)
    event_type_counts = (
        df.groupby("event_type").size().sort_values(ascending=False)
    )
    max_asset_share = float(asset_counts.iloc[0] / rows)
    max_event_type_share = float(event_type_counts.iloc[0] / rows)

    repeated_events = int(
        (df.groupby("event_id").size() > 1).sum()
    )
    unique_origin_days = int(df["origin_trading_day"].nunique())
    sector_fallback_rows = int(
        (df["sector_context_fallback"] > 0.5).sum()
    )

    failures: list[str] = []
    if rows < gates.min_rows:
        failures.append(f"rows<{gates.min_rows}")
    if assets < gates.min_assets:
        failures.append(f"assets<{gates.min_assets}")
    if event_types < gates.min_event_types:
        failures.append(f"event_types<{gates.min_event_types}")
    if unique_events < gates.min_unique_events:
        failures.append(f"unique_events<{gates.min_unique_events}")
    if span_days < gates.min_span_days:
        failures.append(f"span_days<{gates.min_span_days}")
    if max_asset_share > gates.max_asset_share:
        failures.append(
            f"max_asset_share>{gates.max_asset_share:.2f}"
        )
    if max_event_type_share > gates.max_event_type_share:
        failures.append(
            f"max_event_type_share>{gates.max_event_type_share:.2f}"
        )

    return {
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "rows": rows,
        "assets": assets,
        "event_types": event_types,
        "unique_events": unique_events,
        "repeated_events": repeated_events,
        "unique_origin_days": unique_origin_days,
        "first_origin_day": first_day.date().isoformat(),
        "last_origin_day": last_day.date().isoformat(),
        "span_days": span_days,
        "max_asset_share": max_asset_share,
        "max_event_type_share": max_event_type_share,
        "sector_fallback_rows": sector_fallback_rows,
        "asset_counts": {
            str(k): int(v) for k, v in asset_counts.items()
        },
        "event_type_counts": {
            str(k): int(v) for k, v in event_type_counts.items()
        },
    }


def audit_horizon(
    db: Path,
    horizon_sessions: int,
    *,
    gates: DatasetGates = DatasetGates(),
) -> tuple[pd.DataFrame, dict[str, object]]:
    frame = load_dataset(db, horizon_sessions)
    return frame, audit_frame(frame, gates=gates)
