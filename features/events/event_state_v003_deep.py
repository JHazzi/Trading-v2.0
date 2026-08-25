from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = ROOT / "data" / "database" / "market_data_v2.db"

FEATURE_VERSION = "event_state_v003_deep"
STATE_ALGORITHM = "evidence_arrival_snapshots_v003_deep_rebuild"

SEMANTIC_COLUMNS = {
    "observed_fact": "semantic_observed_fact_count",
    "official_statement": "semantic_official_statement_count",
    "reported_fact": "semantic_reported_fact_count",
    "opinion": "semantic_opinion_count",
    "forecast": "semantic_forecast_count",
    "rumor": "semantic_rumor_count",
    "speculation": "semantic_speculation_count",
    "correction": "semantic_correction_count",
    "retraction": "semantic_retraction_count",
    "mixed": "semantic_mixed_count",
    "unknown": "semantic_unknown_count",
}


def canonical_json(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def stable_id(prefix: str, *parts: object) -> str:
    raw = "\0".join(str(p) for p in parts).encode()
    return f"{prefix}_{hashlib.sha256(raw).hexdigest()}"


def parse_utc(value: str) -> datetime:
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError(f"Timestamp sin timezone: {value}")
    return dt.astimezone(timezone.utc)


def _source_for_membership(
    conn: sqlite3.Connection,
    membership_id: str,
) -> str:
    row = conn.execute(
        """
        SELECT r.source_id
        FROM event_cluster_raw_membership_refs AS ref
        JOIN raw_source_documents AS r
          ON r.raw_document_id = ref.raw_document_id
        WHERE ref.membership_id = ?
        """,
        (membership_id,),
    ).fetchone()
    if row is not None:
        return str(row[0])

    row = conn.execute(
        """
        SELECT COALESCE(n.source_provider, n.source_name, 'legacy_news')
        FROM event_cluster_news_membership_refs AS ref
        JOIN news_documents AS n
          ON n.news_id = ref.news_id
        WHERE ref.membership_id = ?
        """,
        (membership_id,),
    ).fetchone()
    return str(row[0]) if row is not None else "unknown"


def build(
    db: Path,
    normalization_run_id: str,
) -> dict[str, object]:
    config = {
        "feature_version": FEATURE_VERSION,
        "algorithm": STATE_ALGORITHM,
        "snapshot_clock": "event_observation_and_each_evidence_arrival",
        "hardcoded_source_reliability": False,
        "hardcoded_event_impact": False,
        "hardcoded_decay": False,
    }
    config_json = canonical_json(config)
    config_sha = hashlib.sha256(config_json.encode()).hexdigest()

    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.row_factory = sqlite3.Row

        run = conn.execute(
            """
            SELECT status
            FROM event_normalization_runs
            WHERE normalization_run_id=?
            """,
            (normalization_run_id,),
        ).fetchone()
        if run is None or run["status"] != "completed":
            raise ValueError(
                f"Normalization run no completado: {normalization_run_id}"
            )

        conn.execute(
            """
            INSERT OR IGNORE INTO event_state_feature_configs(
                feature_version, normalization_version, state_algorithm,
                configuration_sha256, configuration_json
            )
            SELECT ?, normalization_version, ?, ?, ?
            FROM event_normalization_runs
            WHERE normalization_run_id=?
            """,
            (
                FEATURE_VERSION,
                STATE_ALGORITHM,
                config_sha,
                config_json,
                normalization_run_id,
            ),
        )

        base_rows = conn.execute(
            """
            SELECT
                o.event_observation_id,
                o.event_id,
                o.available_at AS observation_available_at,
                o.availability_is_point_in_time,
                v.event_version_id,
                v.event_type,
                v.event_subtype,
                v.event_scope,
                v.occurred_at,
                v.scheduled_for,
                a.asset_id
            FROM normalized_event_observations AS o
            JOIN normalized_event_versions AS v
              ON v.event_version_id = o.event_version_id
            JOIN normalized_event_asset_links AS a
              ON a.event_observation_id = o.event_observation_id
             AND a.normalization_run_id = o.normalization_run_id
            WHERE o.normalization_run_id = ?
            ORDER BY julianday(o.available_at), o.event_id, a.asset_id
            """,
            (normalization_run_id,),
        ).fetchall()

        inserted = 0
        for base in base_rows:
            links = conn.execute(
                """
                SELECT clustering_run_id, cluster_id
                FROM event_cluster_event_links
                WHERE normalization_run_id=?
                  AND event_observation_id=?
                """,
                (
                    normalization_run_id,
                    base["event_observation_id"],
                ),
            ).fetchall()

            evidence_rows = []
            cluster_ids = set()
            for link in links:
                cluster_ids.add(str(link["cluster_id"]))
                members = conn.execute(
                    """
                    SELECT
                        m.membership_id,
                        m.cluster_id,
                        m.evidence_available_at,
                        m.availability_is_point_in_time,
                        COALESCE(s.semantic_type, 'unknown') AS semantic_type
                    FROM event_cluster_memberships AS m
                    LEFT JOIN event_evidence_semantics AS s
                      ON s.normalization_run_id=?
                     AND s.membership_id=m.membership_id
                    WHERE m.clustering_run_id=?
                      AND m.cluster_id=?
                    ORDER BY julianday(m.evidence_available_at), m.decision_order
                    """,
                    (
                        normalization_run_id,
                        link["clustering_run_id"],
                        link["cluster_id"],
                    ),
                ).fetchall()
                evidence_rows.extend(members)

            # De-duplicate memberships if an event has several links to same evidence.
            dedup = {}
            for e in evidence_rows:
                dedup[str(e["membership_id"])] = e
            evidence_rows = list(dedup.values())

            observation_time = str(base["observation_available_at"])
            snapshot_times = {observation_time}
            for e in evidence_rows:
                if parse_utc(str(e["evidence_available_at"])) >= parse_utc(
                    observation_time
                ):
                    snapshot_times.add(str(e["evidence_available_at"]))

            first_evidence = min(
                [observation_time]
                + [str(e["evidence_available_at"]) for e in evidence_rows],
                key=parse_utc,
            )

            for state_time in sorted(snapshot_times, key=parse_utc):
                included = [
                    e
                    for e in evidence_rows
                    if parse_utc(str(e["evidence_available_at"]))
                    <= parse_utc(state_time)
                ]
                semantic_counts = Counter(
                    str(e["semantic_type"]) for e in included
                )
                sources = sorted(
                    {
                        _source_for_membership(
                            conn, str(e["membership_id"])
                        )
                        for e in included
                    }
                )
                clusters_seen = {
                    str(e["cluster_id"]) for e in included
                }
                pit_fraction = (
                    sum(int(e["availability_is_point_in_time"]) for e in included)
                    / len(included)
                    if included
                    else float(base["availability_is_point_in_time"])
                )

                state_dt = parse_utc(state_time)
                first_dt = parse_utc(first_evidence)
                occurred = base["occurred_at"]
                scheduled = base["scheduled_for"]

                event_age = None
                if occurred is not None:
                    event_age = (
                        state_dt - parse_utc(str(occurred))
                    ).total_seconds()

                time_to_scheduled = None
                if scheduled is not None:
                    time_to_scheduled = (
                        parse_utc(str(scheduled)) - state_dt
                    ).total_seconds()

                values = {
                    column: int(semantic_counts.get(semantic, 0))
                    for semantic, column in SEMANTIC_COLUMNS.items()
                }

                event_state_id = stable_id(
                    "est",
                    base["event_id"],
                    base["asset_id"],
                    state_time,
                    FEATURE_VERSION,
                )

                params = (
                    event_state_id,
                    normalization_run_id,
                    base["event_observation_id"],
                    base["event_id"],
                    int(base["asset_id"]),
                    state_time,
                    state_time,
                    first_evidence,
                    base["event_type"],
                    base["event_subtype"],
                    base["event_scope"],
                    "|".join(sources) if sources else "unknown",
                    len(included),
                    len(clusters_seen),
                    len(sources),
                    pit_fraction,
                    values["semantic_observed_fact_count"],
                    values["semantic_official_statement_count"],
                    values["semantic_reported_fact_count"],
                    values["semantic_opinion_count"],
                    values["semantic_forecast_count"],
                    values["semantic_rumor_count"],
                    values["semantic_speculation_count"],
                    values["semantic_correction_count"],
                    values["semantic_retraction_count"],
                    values["semantic_mixed_count"],
                    values["semantic_unknown_count"],
                    max(0.0, (state_dt - first_dt).total_seconds()),
                    event_age,
                    time_to_scheduled,
                    int(occurred is not None),
                    int(scheduled is not None),
                    FEATURE_VERSION,
                    canonical_json({
                        "state_is_factual_context_only": True,
                        "source_reliability_not_encoded": True,
                        "impact_not_encoded": True,
                        "deep_corpus_rebuild": True,
                    }),
                )
                inserted += int(
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO normalized_event_state_snapshots(
                            event_state_id, normalization_run_id,
                            event_observation_id, event_id, asset_id,
                            state_time, available_at, first_evidence_at,
                            event_type, event_subtype, event_scope,
                            source_signature, evidence_count,
                            distinct_cluster_count, distinct_source_count,
                            point_in_time_evidence_fraction,
                            semantic_observed_fact_count,
                            semantic_official_statement_count,
                            semantic_reported_fact_count,
                            semantic_opinion_count,
                            semantic_forecast_count,
                            semantic_rumor_count,
                            semantic_speculation_count,
                            semantic_correction_count,
                            semantic_retraction_count,
                            semantic_mixed_count,
                            semantic_unknown_count,
                            seconds_since_first_evidence,
                            event_age_seconds, time_to_scheduled_seconds,
                            has_known_occurrence_time, has_scheduled_time,
                            feature_version, metadata_json
                        ) VALUES (
                            ?,?,?,?,?, ?,?,?,?,?, ?,?,?,?,?, ?,
                            ?,?,?,?,?, ?,?,?,?,?, ?,
                            ?,?,?,?,?, ?,?
                        )
                        """,
                        params,
                    ).rowcount
                    == 1
                )

        conn.commit()
        total = conn.execute(
            """
            SELECT COUNT(*)
            FROM normalized_event_state_snapshots
            WHERE normalization_run_id=? AND feature_version=?
            """,
            (normalization_run_id, FEATURE_VERSION),
        ).fetchone()[0]

    return {
        "normalization_run_id": normalization_run_id,
        "feature_version": FEATURE_VERSION,
        "inserted": inserted,
        "total": int(total),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--normalization-run-id", required=True)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = ap.parse_args()
    print(
        json.dumps(
            build(args.db, args.normalization_run_id),
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
