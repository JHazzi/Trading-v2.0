from __future__ import annotations

import sqlite3
from pathlib import Path

from models.events.dataset_v002 import load_dataset
from evaluation.events.audit_v002 import audit_frame

EVENT_FEATURE_VERSION = "event_state_v003_deep"
LABEL_VERSION = "event_reaction_daily_v003_deep"
NORMALIZATION_VERSION = "sec_event_normalizer_v003_deep_rebuild"
RUN_PREFIX = "eventbrain_deep_v003_"


def model_ready_audit(db: Path) -> dict[str, object]:
    output: dict[str, object] = {}
    for horizon in (1, 3, 5, 10):
        frame = load_dataset(
            db,
            horizon,
            event_feature_version=EVENT_FEATURE_VERSION,
            label_version=LABEL_VERSION,
        )
        output[str(horizon)] = audit_frame(frame)
    return output


def corpus_audit(
    db: Path,
    *,
    common_start: str,
    common_end_inclusive: str,
) -> dict[str, object]:
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row

        cluster_rows = conn.execute(
            """
            SELECT
                r.clustering_run_id,
                r.status,
                r.documents_considered,
                r.memberships_written,
                r.clusters_created,
                COUNT(DISTINCT m.cluster_id) AS effective_clusters,
                COUNT(m.membership_id) AS memberships,
                COALESCE(SUM(m.availability_is_point_in_time),0) AS pit_memberships
            FROM event_clustering_runs r
            LEFT JOIN event_cluster_memberships m
              ON m.clustering_run_id=r.clustering_run_id
            WHERE r.clustering_run_id LIKE ?
            GROUP BY
                r.clustering_run_id,r.status,r.documents_considered,
                r.memberships_written,r.clusters_created
            ORDER BY r.clustering_run_id
            """,
            (f"{RUN_PREFIX}%",),
        ).fetchall()

        normalized = conn.execute(
            """
            SELECT
                COUNT(DISTINCT nr.normalization_run_id) AS runs,
                COUNT(DISTINCT o.event_id) AS unique_events,
                COUNT(*) AS observations,
                MIN(o.available_at) AS first_available,
                MAX(o.available_at) AS last_available,
                COALESCE(SUM(o.availability_is_point_in_time),0) AS pit_observations
            FROM event_normalization_runs nr
            JOIN normalized_event_observations o
              ON o.normalization_run_id=nr.normalization_run_id
            WHERE nr.normalization_version=?
              AND nr.clustering_run_id LIKE ?
            """,
            (NORMALIZATION_VERSION, f"{RUN_PREFIX}%"),
        ).fetchone()

        overlap = conn.execute(
            """
            WITH deep_events AS (
                SELECT DISTINCT o.event_id
                FROM event_normalization_runs nr
                JOIN normalized_event_observations o
                  ON o.normalization_run_id=nr.normalization_run_id
                WHERE nr.normalization_version=?
                  AND nr.clustering_run_id LIKE ?
            )
            SELECT
                COUNT(*) AS deep_events,
                SUM(CASE WHEN EXISTS (
                    SELECT 1
                    FROM normalized_event_observations old
                    JOIN event_normalization_runs old_nr
                      ON old_nr.normalization_run_id=old.normalization_run_id
                    WHERE old.event_id=d.event_id
                      AND old_nr.normalization_version<>?
                ) THEN 1 ELSE 0 END) AS reused_existing_event_identities
            FROM deep_events d
            """,
            (
                NORMALIZATION_VERSION,
                f"{RUN_PREFIX}%",
                NORMALIZATION_VERSION,
            ),
        ).fetchone()

        states = conn.execute(
            """
            SELECT
                COUNT(*) AS states,
                COUNT(DISTINCT event_id) AS events,
                COUNT(DISTINCT asset_id) AS assets,
                MIN(state_time) AS first_state,
                MAX(state_time) AS last_state,
                COALESCE(SUM(
                    CASE WHEN point_in_time_evidence_fraction=1.0
                         THEN 1 ELSE 0 END
                ),0) AS strict_pit_states
            FROM normalized_event_state_snapshots
            WHERE feature_version=?
            """,
            (EVENT_FEATURE_VERSION,),
        ).fetchone()

        by_ticker = conn.execute(
            """
            SELECT
                a.ticker,
                COUNT(*) AS states,
                COUNT(DISTINCT s.event_id) AS events,
                MIN(s.state_time) AS first_state,
                MAX(s.state_time) AS last_state
            FROM normalized_event_state_snapshots s
            JOIN assets a ON a.asset_id=s.asset_id
            WHERE s.feature_version=?
            GROUP BY a.asset_id,a.ticker
            ORDER BY a.ticker
            """,
            (EVENT_FEATURE_VERSION,),
        ).fetchall()

        by_year = conn.execute(
            """
            SELECT
                substr(s.state_time,1,4) AS year,
                COUNT(*) AS states,
                COUNT(DISTINCT s.event_id) AS events,
                COUNT(DISTINCT s.asset_id) AS assets
            FROM normalized_event_state_snapshots s
            WHERE s.feature_version=?
            GROUP BY substr(s.state_time,1,4)
            ORDER BY year
            """,
            (EVENT_FEATURE_VERSION,),
        ).fetchall()

        event_types = conn.execute(
            """
            SELECT event_type, COUNT(DISTINCT event_id) AS events
            FROM normalized_event_state_snapshots
            WHERE feature_version=?
            GROUP BY event_type
            ORDER BY events DESC,event_type
            """,
            (EVENT_FEATURE_VERSION,),
        ).fetchall()

        label_status = conn.execute(
            """
            SELECT horizon_sessions,label_status,COUNT(*) AS n
            FROM normalized_event_reaction_labels
            WHERE label_version=?
            GROUP BY horizon_sessions,label_status
            ORDER BY horizon_sessions,label_status
            """,
            (LABEL_VERSION,),
        ).fetchall()

        before_window = conn.execute(
            """
            SELECT COUNT(*)
            FROM normalized_event_state_snapshots
            WHERE feature_version=?
              AND substr(state_time,1,10) < ?
            """,
            (EVENT_FEATURE_VERSION, common_start),
        ).fetchone()[0]

        after_window = conn.execute(
            """
            SELECT COUNT(*)
            FROM normalized_event_state_snapshots
            WHERE feature_version=?
              AND substr(state_time,1,10) > ?
            """,
            (EVENT_FEATURE_VERSION, common_end_inclusive),
        ).fetchone()[0]

    unique_events = int(normalized["unique_events"] or 0)
    reused = int(overlap["reused_existing_event_identities"] or 0)
    failures: list[str] = []

    if len(cluster_rows) not in {0, 10}:
        failures.append(f"cluster_runs={len(cluster_rows)} expected=10")
    if cluster_rows and any(str(r["status"]) != "completed" for r in cluster_rows):
        failures.append("non_completed_clustering_run")
    if int(before_window) != 0:
        failures.append(f"states_before_common_window={before_window}")
    if int(after_window) != 0:
        failures.append(f"states_after_common_window={after_window}")
    if states["states"] and int(states["assets"] or 0) < 9:
        failures.append(f"state_assets={states['assets']} expected>=9")

    result = {
        "status": "PASS" if not failures else "REVIEW",
        "failures": failures,
        "versions": {
            "normalization": NORMALIZATION_VERSION,
            "state": EVENT_FEATURE_VERSION,
            "labels": LABEL_VERSION,
        },
        "window": {
            "common_start": common_start,
            "common_end_inclusive": common_end_inclusive,
            "states_before_window": int(before_window),
            "states_after_window": int(after_window),
        },
        "clustering": [dict(x) for x in cluster_rows],
        "normalized": dict(normalized),
        "lineage": {
            "unique_deep_events": unique_events,
            "reused_existing_event_identities": reused,
            "new_event_identities": max(0, unique_events - reused),
            "stable_identity_contract": "sec_accession_item_v001",
        },
        "states": dict(states),
        "states_by_ticker": [dict(x) for x in by_ticker],
        "states_by_year": [dict(x) for x in by_year],
        "event_types": [dict(x) for x in event_types],
        "labels": [dict(x) for x in label_status],
        "research_scale_target": {
            "target_unique_events": 1000,
            "met": unique_events >= 1000,
        },
    }

    if int(states["states"] or 0) > 0 and label_status:
        result["model_ready"] = model_ready_audit(db)

    return result
