from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from ingestion.events.sec_event_normalizer_v002 import (
    NORMALIZATION_VERSION,
    normalize,
)
from features.events.event_state_v002 import (
    FEATURE_VERSION,
    build as build_states,
)
from evaluation.targets.event_reaction_targets_v002 import (
    LABEL_VERSION,
    build as build_labels,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "database" / "market_data_v2.db"
RUN_PREFIX = "eventbrain_scale_"


def clustering_runs(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """
        SELECT clustering_run_id
        FROM event_clustering_runs
        WHERE clustering_run_id LIKE ?
          AND status='completed'
        ORDER BY clustering_run_id
        """,
        (f"{RUN_PREFIX}%",),
    ).fetchall()
    return [str(r[0]) for r in rows]


def normalization_runs(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    rows = conn.execute(
        """
        SELECT clustering_run_id, normalization_run_id
        FROM event_normalization_runs
        WHERE normalization_version=?
          AND clustering_run_id LIKE ?
          AND status='completed'
        ORDER BY clustering_run_id
        """,
        (NORMALIZATION_VERSION, f"{RUN_PREFIX}%"),
    ).fetchall()
    return [(str(a), str(b)) for a, b in rows]


def do_normalize(db: Path) -> list[dict]:
    with sqlite3.connect(db) as conn:
        runs = clustering_runs(conn)
    if len(runs) != 10:
        raise RuntimeError(
            f"Esperaba 10 clustering runs del piloto; encontré {len(runs)}"
        )

    results = []
    for run_id in runs:
        result = normalize(db, run_id)
        results.append(result)
        print(json.dumps(
            {"clustering_run_id": run_id, **result},
            ensure_ascii=False,
        ))
    return results


def do_states(db: Path) -> list[dict]:
    with sqlite3.connect(db) as conn:
        runs = normalization_runs(conn)
    if len(runs) != 10:
        raise RuntimeError(
            f"Esperaba 10 normalization runs v002; encontré {len(runs)}"
        )

    results = []
    for clustering_run_id, normalization_run_id in runs:
        result = build_states(db, normalization_run_id)
        results.append(result)
        print(json.dumps(
            {
                "clustering_run_id": clustering_run_id,
                **result,
            },
            ensure_ascii=False,
        ))
    return results


def do_labels(db: Path) -> dict:
    return build_labels(
        db,
        feature_version=FEATURE_VERSION,
        horizons=(1, 3, 5, 10),
        include_intraday_coarse=False,
    )


def audit(db: Path) -> dict:
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row

        runs = conn.execute(
            """
            SELECT
                r.clustering_run_id,
                r.documents_considered,
                COUNT(DISTINCT m.cluster_id) AS cluster_count,
                SUM(m.availability_is_point_in_time) AS pit_memberships,
                COUNT(*) AS memberships
            FROM event_clustering_runs AS r
            JOIN event_cluster_memberships AS m
              ON m.clustering_run_id=r.clustering_run_id
            WHERE r.clustering_run_id LIKE ?
            GROUP BY r.clustering_run_id, r.documents_considered
            ORDER BY r.clustering_run_id
            """,
            (f"{RUN_PREFIX}%",),
        ).fetchall()

        normalized = conn.execute(
            """
            SELECT
                COUNT(DISTINCT nr.normalization_run_id) AS runs,
                COUNT(DISTINCT o.event_id) AS events,
                COUNT(*) AS event_observations,
                SUM(o.availability_is_point_in_time) AS pit_event_observations,
                MIN(o.available_at) AS first_available,
                MAX(o.available_at) AS last_available
            FROM event_normalization_runs nr
            JOIN normalized_event_observations o
              ON o.normalization_run_id=nr.normalization_run_id
            WHERE nr.normalization_version=?
              AND nr.clustering_run_id LIKE ?
            """,
            (NORMALIZATION_VERSION, f"{RUN_PREFIX}%"),
        ).fetchone()

        by_asset = conn.execute(
            """
            SELECT
                a.ticker,
                COUNT(*) AS states,
                COUNT(DISTINCT s.event_id) AS events,
                SUM(CASE
                    WHEN s.point_in_time_evidence_fraction=1.0 THEN 1
                    ELSE 0
                END) AS strict_pit_states
            FROM normalized_event_state_snapshots s
            JOIN assets a ON a.asset_id=s.asset_id
            WHERE s.feature_version=?
            GROUP BY a.asset_id, a.ticker
            ORDER BY a.ticker
            """,
            (FEATURE_VERSION,),
        ).fetchall()

        event_types = conn.execute(
            """
            SELECT v.event_type, COUNT(DISTINCT s.event_id) AS events
            FROM normalized_event_state_snapshots s
            JOIN normalized_event_observations o
              ON o.event_observation_id=s.event_observation_id
            JOIN normalized_event_versions v
              ON v.event_version_id=o.event_version_id
            WHERE s.feature_version=?
            GROUP BY v.event_type
            ORDER BY events DESC, v.event_type
            """,
            (FEATURE_VERSION,),
        ).fetchall()

        labels = conn.execute(
            """
            SELECT horizon_sessions, label_status, COUNT(*) AS n
            FROM normalized_event_reaction_labels
            WHERE label_version=?
            GROUP BY horizon_sessions, label_status
            ORDER BY horizon_sessions, label_status
            """,
            (LABEL_VERSION,),
        ).fetchall()

        suspicious = conn.execute(
            """
            SELECT COUNT(*)
            FROM normalized_event_observations o
            JOIN event_normalization_runs nr
              ON nr.normalization_run_id=o.normalization_run_id
            WHERE nr.normalization_version=?
              AND nr.clustering_run_id LIKE ?
              AND julianday(o.available_at) >
                  julianday('2026-08-24T23:59:59+00:00')
            """,
            (NORMALIZATION_VERSION, f"{RUN_PREFIX}%"),
        ).fetchone()[0]

    return {
        "normalization_version": NORMALIZATION_VERSION,
        "feature_version": FEATURE_VERSION,
        "label_version": LABEL_VERSION,
        "clustering": [dict(r) for r in runs],
        "normalized": dict(normalized),
        "states_by_asset": [dict(r) for r in by_asset],
        "event_types": [dict(r) for r in event_types],
        "labels": [dict(r) for r in labels],
        "future_dated_event_observations_after_2026_08_24":
            int(suspicious),
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--stage",
        choices=("normalize", "states", "labels", "audit", "all"),
        required=True,
    )
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = p.parse_args()

    if args.stage in {"normalize", "all"}:
        do_normalize(args.db)
    if args.stage in {"states", "all"}:
        do_states(args.db)
    if args.stage in {"labels", "all"}:
        print(json.dumps(do_labels(args.db), indent=2, ensure_ascii=False))
    print(json.dumps(audit(args.db), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
