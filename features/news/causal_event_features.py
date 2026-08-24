from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

import pandas as pd


FEATURE_VERSION = "event_features_v0.2.0"
MAX_EVENTS_PER_SNAPSHOT = 50


def available_events(
    conn: sqlite3.Connection,
    *,
    asset_id: int,
    as_of: str,
) -> pd.DataFrame:
    """Return the latest causal state per event for one asset.

    Event metadata lives in ``events``, temporal snapshots in
    ``event_states``, and canonical asset links in ``event_assets``.
    Both state_time and available_at must be no later than the prediction
    timestamp.
    """
    query = """
        WITH eligible AS (
            SELECT
                es.event_state_id,
                es.event_id,
                es.asset_id AS state_asset_id,
                es.state_time,
                es.available_at,
                es.novelty,
                es.evidence_count,
                es.source_diversity,
                es.uncertainty,
                es.expected_surprise,
                COALESCE(es.expected_direction, ea.expected_direction)
                    AS expected_direction,
                es.event_persistence,
                es.event_age_seconds,
                es.future_event_flag,
                es.feature_version AS state_feature_version,
                es.metadata_json AS state_metadata_json,
                e.event_type,
                e.canonical_title,
                e.event_time,
                e.event_scope,
                e.status AS event_status,
                e.metadata_json AS event_metadata_json,
                ea.relevance,
                ROW_NUMBER() OVER (
                    PARTITION BY es.event_id, es.feature_version
                    ORDER BY es.state_time DESC,
                             es.available_at DESC,
                             es.event_state_id DESC
                ) AS causal_rank
            FROM event_states es
            JOIN events e
              ON e.event_id = es.event_id
            LEFT JOIN event_assets ea
              ON ea.event_id = es.event_id
             AND ea.asset_id = ?
            WHERE es.available_at <= ?
              AND es.state_time <= ?
              AND (
                    es.asset_id = ?
                    OR (es.asset_id IS NULL AND ea.asset_id IS NOT NULL)
              )
        )
        SELECT *
        FROM eligible
        WHERE causal_rank = 1
        ORDER BY
            COALESCE(relevance, 0.0) DESC,
            COALESCE(novelty, 0.0) DESC,
            COALESCE(uncertainty, 0.0) DESC,
            available_at DESC
    """
    return pd.read_sql_query(
        query,
        conn,
        params=(asset_id, as_of, as_of, asset_id),
    )


def _optional_float(value: object) -> float | None:
    return None if pd.isna(value) else float(value)


def _optional_int(value: object) -> int | None:
    return None if pd.isna(value) else int(value)


def build_event_snapshot(
    db: Path,
    asset_id: int,
    as_of: str,
) -> dict:
    """Build a deterministic causal event snapshot for one asset.

    This reader exposes event state and evidence metadata. It does not turn an
    event into a hardcoded market impact or impose a decay curve.
    """
    as_of_ts = pd.to_datetime(as_of, utc=True)
    normalized_as_of = as_of_ts.isoformat()

    with sqlite3.connect(db) as conn:
        events = available_events(
            conn,
            asset_id=asset_id,
            as_of=normalized_as_of,
        )

    if events.empty:
        return {
            "asset_id": asset_id,
            "as_of": normalized_as_of,
            "feature_version": FEATURE_VERSION,
            "event_count": 0,
            "returned_event_count": 0,
            "truncated": False,
            "events": [],
        }

    events["available_at"] = pd.to_datetime(events["available_at"], utc=True)
    events["age_seconds_as_of"] = (
        as_of_ts - events["available_at"]
    ).dt.total_seconds().clip(lower=0)

    records = []
    for row in events.head(MAX_EVENTS_PER_SNAPSHOT).itertuples(index=False):
        records.append(
            {
                "event_id": row.event_id,
                "event_type": row.event_type,
                "canonical_title": row.canonical_title,
                "event_time": row.event_time,
                "event_scope": row.event_scope,
                "event_status": row.event_status,
                "state_time": row.state_time,
                "available_at": row.available_at.isoformat(),
                "state_feature_version": row.state_feature_version,
                "relevance": _optional_float(row.relevance),
                "novelty": _optional_float(row.novelty),
                "evidence_count": _optional_int(row.evidence_count),
                "source_diversity": _optional_float(row.source_diversity),
                "uncertainty": _optional_float(row.uncertainty),
                "expected_surprise": _optional_float(row.expected_surprise),
                "expected_direction": _optional_float(row.expected_direction),
                "event_persistence": _optional_float(row.event_persistence),
                "event_age_seconds_at_state": _optional_float(
                    row.event_age_seconds
                ),
                "age_seconds_as_of": float(row.age_seconds_as_of),
                "future_event_flag": bool(row.future_event_flag),
                "state_metadata_json": row.state_metadata_json,
                "event_metadata_json": row.event_metadata_json,
            }
        )

    total = len(events)
    return {
        "asset_id": asset_id,
        "as_of": normalized_as_of,
        "feature_version": FEATURE_VERSION,
        "event_count": total,
        "returned_event_count": len(records),
        "truncated": total > len(records),
        "events": records,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--asset-id", type=int, required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--db", default="data/database/market_data_v2.db")
    args = parser.parse_args()

    result = build_event_snapshot(Path(args.db), args.asset_id, args.as_of)
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
