from __future__ import annotations

import sqlite3
from pathlib import Path

from models.events.dataset_v002 import load_dataset
from evaluation.events.audit_v002 import audit_frame

EVENT_FEATURE_VERSION = "event_state_v0031_deep"
LABEL_VERSION = "event_reaction_daily_v0031_deep"
NORMALIZATION_VERSION = "sec_event_normalizer_v0031_deep_raw_lineage"
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
    expected_filings: int | None = None,
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

        normalization_runs = conn.execute(
            """
            WITH semantic_counts AS (
                SELECT
                    normalization_run_id,
                    COUNT(*) AS persisted_evidence_semantics
                FROM event_evidence_semantics
                GROUP BY normalization_run_id
            ),
            filing_counts AS (
                SELECT
                    ees.normalization_run_id,
                    COUNT(DISTINCT fv.filing_raw_document_id)
                        AS persisted_filings_with_evidence
                FROM event_evidence_semantics AS ees
                JOIN event_cluster_raw_membership_refs AS rr
                  ON rr.membership_id=ees.membership_id
                JOIN sec_filing_file_versions AS fv
                  ON fv.raw_document_id=rr.raw_document_id
                GROUP BY ees.normalization_run_id
            )
            SELECT
                nr.normalization_run_id,
                nr.clustering_run_id,
                nr.status,
                nr.clusters_considered,
                nr.events_observed,
                nr.evidence_semantics_written,
                COALESCE(sc.persisted_evidence_semantics,0)
                    AS persisted_evidence_semantics,
                COALESCE(fc.persisted_filings_with_evidence,0)
                    AS persisted_filings_with_evidence,
                nr.started_at,
                nr.finished_at
            FROM event_normalization_runs AS nr
            LEFT JOIN semantic_counts AS sc
              ON sc.normalization_run_id=nr.normalization_run_id
            LEFT JOIN filing_counts AS fc
              ON fc.normalization_run_id=nr.normalization_run_id
            WHERE nr.normalization_version=?
              AND nr.clustering_run_id LIKE ?
            ORDER BY nr.clustering_run_id
            """,
            (NORMALIZATION_VERSION, f"{RUN_PREFIX}%"),
        ).fetchall()

        normalized = conn.execute(
            """
            SELECT
                COUNT(DISTINCT o.event_id) AS unique_events,
                COUNT(*) AS observations,
                MIN(o.available_at) AS first_available,
                MAX(o.available_at) AS last_available,
                COALESCE(SUM(o.availability_is_point_in_time),0) AS pit_observations
            FROM normalized_event_observations o
            JOIN event_normalization_runs nr
              ON nr.normalization_run_id=o.normalization_run_id
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

    clustering_failures: list[str] = []
    if len(cluster_rows) != 10:
        clustering_failures.append(
            f"cluster_runs={len(cluster_rows)} expected=10"
        )
    if cluster_rows and any(
        str(r["status"]) != "completed"
        for r in cluster_rows
    ):
        clustering_failures.append("non_completed_clustering_run")
    clustering_status = (
        "PASS" if not clustering_failures else "REVIEW"
    )

    normalization_failures: list[str] = []
    normalization_status = "NOT_RUN"
    normalization_coverage = None
    if normalization_runs:
        normalization_status = "PASS"
        if len(normalization_runs) != 10:
            normalization_failures.append(
                f"normalization_runs={len(normalization_runs)} expected=10"
            )
        incomplete = [
            str(r["clustering_run_id"])
            for r in normalization_runs
            if str(r["status"]) != "completed"
        ]
        if incomplete:
            normalization_failures.append(
                f"non_completed_normalization_runs={incomplete}"
            )
        zero_output = [
            str(r["clustering_run_id"])
            for r in normalization_runs
            if int(r["persisted_filings_with_evidence"] or 0) == 0
            or int(r["events_observed"] or 0) == 0
        ]
        if zero_output:
            normalization_failures.append(
                f"zero_output_runs={zero_output}"
            )
        considered = sum(
            int(r["persisted_filings_with_evidence"] or 0)
            for r in normalization_runs
        )

        semantic_count_mismatches = [
            {
                "clustering_run_id": str(r["clustering_run_id"]),
                "run_recorded": int(r["evidence_semantics_written"] or 0),
                "persisted": int(r["persisted_evidence_semantics"] or 0),
            }
            for r in normalization_runs
            if int(r["evidence_semantics_written"] or 0)
            != int(r["persisted_evidence_semantics"] or 0)
        ]
        if semantic_count_mismatches:
            normalization_failures.append(
                "evidence_semantics_persistence_mismatch="
                + str(semantic_count_mismatches)
            )
        if expected_filings:
            normalization_coverage = considered / float(expected_filings)
            # Pipeline completeness gate, not a predictive target.
            # This count is reconstructed from persisted evidence semantics,
            # i.e. AFTER the V003.1 form guard.
            if normalization_coverage < 0.95:
                normalization_failures.append(
                    "normalization_filing_coverage<0.95"
                )
        if unique_events == 0:
            normalization_failures.append("unique_events=0")
        if normalization_failures:
            normalization_status = "REVIEW"

    state_failures: list[str] = []
    state_status = "NOT_RUN"
    if int(states["states"] or 0) > 0:
        state_status = "PASS"
        if int(before_window) != 0:
            state_failures.append(
                f"states_before_common_window={before_window}"
            )
        if int(after_window) != 0:
            state_failures.append(
                f"states_after_common_window={after_window}"
            )
        if int(states["assets"] or 0) < 9:
            state_failures.append(
                f"state_assets={states['assets']} expected>=9"
            )
        if state_failures:
            state_status = "REVIEW"

    label_status_name = "NOT_RUN" if not label_status else "PASS"

    # Overall status follows the deepest stage that has actually run.
    if label_status:
        overall_status = label_status_name
        overall_failures = []
    elif int(states["states"] or 0) > 0:
        overall_status = state_status
        overall_failures = state_failures
    elif normalization_runs:
        overall_status = normalization_status
        overall_failures = normalization_failures
    else:
        overall_status = clustering_status
        overall_failures = clustering_failures

    result = {
        "status": overall_status,
        "failures": overall_failures,
        "stage_status": {
            "clustering": {
                "status": clustering_status,
                "failures": clustering_failures,
            },
            "normalization": {
                "status": normalization_status,
                "failures": normalization_failures,
                "filing_coverage_vs_expected": normalization_coverage,
                "persisted_filings_with_evidence": sum(
                    int(r["persisted_filings_with_evidence"] or 0)
                    for r in normalization_runs
                ),
                "persisted_evidence_semantics": sum(
                    int(r["persisted_evidence_semantics"] or 0)
                    for r in normalization_runs
                ),
            },
            "states": {
                "status": state_status,
                "failures": state_failures,
            },
            "labels": {
                "status": label_status_name,
                "failures": [],
            },
        },
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
        "normalization_runs": [dict(x) for x in normalization_runs],
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
