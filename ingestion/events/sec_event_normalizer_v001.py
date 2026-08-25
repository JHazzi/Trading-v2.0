from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = ROOT / "data" / "database" / "market_data_v2.db"

NORMALIZATION_VERSION = "sec_event_normalizer_v001"
TAXONOMY_VERSION = "sec_factual_taxonomy_v001"
SEMANTIC_SCHEMA_VERSION = "event_evidence_semantics_v001"
PARSER_VERSION = "sec_event_normalizer_v0.1.0"

# Factual taxonomy only. No market direction or importance is encoded.
ITEM_TAXONOMY = {
    "1.01": "material_definitive_agreement",
    "1.02": "termination_material_agreement",
    "1.03": "bankruptcy_or_receivership_disclosure",
    "2.01": "asset_acquisition_or_disposition",
    "2.02": "financial_results_disclosure",
    "2.03": "direct_financial_obligation",
    "2.04": "obligation_acceleration_trigger",
    "2.05": "exit_or_disposal_costs",
    "2.06": "material_impairment",
    "3.01": "listing_or_compliance_notice",
    "3.02": "unregistered_equity_sale",
    "3.03": "security_holder_rights_change",
    "4.01": "auditor_change",
    "4.02": "financial_statement_nonreliance",
    "5.01": "change_in_control",
    "5.02": "management_or_board_change",
    "5.03": "charter_or_bylaw_change",
    "5.07": "shareholder_vote_result",
    "7.01": "regulation_fd_disclosure",
    "8.01": "other_material_disclosure",
}

FORM_TAXONOMY = {
    "10-K": "annual_report_disclosure",
    "10-K/A": "annual_report_amendment",
    "10-Q": "quarterly_report_disclosure",
    "10-Q/A": "quarterly_report_amendment",
    "6-K": "foreign_issuer_report_disclosure",
    "20-F": "foreign_issuer_annual_report",
    "4": "insider_ownership_disclosure",
    "4/A": "insider_ownership_disclosure_amendment",
    "SC 13D": "beneficial_ownership_disclosure",
    "SC 13G": "beneficial_ownership_disclosure",
    "S-1": "registration_statement",
    "S-3": "registration_statement",
}

REQUIRED_TABLES = {
    "event_clustering_runs",
    "event_cluster_memberships",
    "event_cluster_sec_observation_refs",
    "sec_filing_file_observations",
    "sec_filing_metadata_observations",
    "sec_filing_metadata_versions",
    "raw_document_assets",
    "normalized_event_identities",
    "normalized_event_versions",
    "normalized_event_observations",
    "event_cluster_event_links",
    "event_evidence_semantics",
    "normalized_event_entity_links",
    "normalized_event_asset_links",
    "event_normalization_configs",
    "event_normalization_runs",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def stable_id(prefix: str, *parts: object) -> str:
    raw = "\0".join(str(p) for p in parts).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(raw).hexdigest()}"


def parse_items(items_json: str | None) -> list[str]:
    if not items_json:
        return []
    try:
        value = json.loads(items_json)
    except json.JSONDecodeError:
        value = items_json

    if isinstance(value, str):
        values: Iterable[object] = [
            part.strip()
            for part in value.replace(";", ",").split(",")
            if part.strip()
        ]
    elif isinstance(value, dict):
        values = list(value.keys())
    elif isinstance(value, list):
        values = value
    else:
        values = []

    out: list[str] = []
    for item in values:
        text = str(item).strip()
        if text.lower().startswith("item "):
            text = text[5:].strip()
        text = text.rstrip(".")
        if text and text not in out:
            out.append(text)
    return out


def factual_events_for_filing(form: str, items_json: str | None) -> list[dict[str, str | None]]:
    normalized_form = form.strip().upper()
    if normalized_form in {"8-K", "8-K/A"}:
        items = [item for item in parse_items(items_json) if item != "9.01"]
        if items:
            return [
                {
                    "identity_suffix": f"item:{item}",
                    "event_type": ITEM_TAXONOMY.get(
                        item, "current_report_item_disclosure"
                    ),
                    "event_subtype": f"{normalized_form}:item:{item}",
                }
                for item in items
            ]
        return [{
            "identity_suffix": "current_report",
            "event_type": "current_report_disclosure",
            "event_subtype": normalized_form,
        }]

    return [{
        "identity_suffix": f"form:{normalized_form}",
        "event_type": FORM_TAXONOMY.get(
            normalized_form, "sec_filing_disclosure"
        ),
        "event_subtype": normalized_form,
    }]


def ensure_contract(conn: sqlite3.Connection) -> None:
    tables = {
        str(r[0])
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    missing = sorted(REQUIRED_TABLES - tables)
    if missing:
        raise RuntimeError(f"Contrato SEC normalizer incompleto: {missing}")


@dataclass(frozen=True)
class FilingEvidence:
    cluster_id: str
    membership_id: str
    evidence_available_at: str
    evidence_pit: int
    filing_raw_document_id: str


def _run_info(conn: sqlite3.Connection, clustering_run_id: str) -> sqlite3.Row:
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        """
        SELECT *
        FROM event_clustering_runs
        WHERE clustering_run_id = ?
        """,
        (clustering_run_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Clustering run inexistente: {clustering_run_id}")
    if row["status"] != "completed":
        raise ValueError(
            f"Clustering run no completado: {clustering_run_id} status={row['status']}"
        )
    return row


def _sec_evidence(
    conn: sqlite3.Connection,
    clustering_run_id: str,
    as_of: str,
) -> list[FilingEvidence]:
    rows = conn.execute(
        """
        SELECT DISTINCT
            m.cluster_id,
            m.membership_id,
            m.evidence_available_at,
            m.availability_is_point_in_time,
            fo.filing_raw_document_id
        FROM event_cluster_memberships AS m
        JOIN event_cluster_sec_observation_refs AS sor
          ON sor.membership_id = m.membership_id
        JOIN sec_filing_file_observations AS fo
          ON fo.observation_id = sor.observation_id
        WHERE m.clustering_run_id = ?
          AND julianday(m.evidence_available_at) <= julianday(?)
        ORDER BY julianday(m.evidence_available_at), m.decision_order
        """,
        (clustering_run_id, as_of),
    ).fetchall()
    return [
        FilingEvidence(
            cluster_id=str(r[0]),
            membership_id=str(r[1]),
            evidence_available_at=str(r[2]),
            evidence_pit=int(r[3]),
            filing_raw_document_id=str(r[4]),
        )
        for r in rows
    ]


def _metadata_asof(
    conn: sqlite3.Connection,
    filing_raw_document_id: str,
    as_of: str,
) -> sqlite3.Row | None:
    conn.row_factory = sqlite3.Row
    return conn.execute(
        """
        SELECT
            mo.metadata_observation_id,
            mo.available_at,
            mo.availability_is_point_in_time,
            mo.observation_sequence,
            mo.observation_kind,
            mv.*
        FROM sec_filing_metadata_observations AS mo
        JOIN sec_filing_metadata_versions AS mv
          ON mv.metadata_version_id = mo.metadata_version_id
         AND mv.filing_raw_document_id = mo.filing_raw_document_id
        WHERE mo.filing_raw_document_id = ?
          AND julianday(mo.available_at) <= julianday(?)
        ORDER BY mo.observation_sequence DESC
        LIMIT 1
        """,
        (filing_raw_document_id, as_of),
    ).fetchone()


def _assets_for_filing(
    conn: sqlite3.Connection,
    filing_raw_document_id: str,
    ticker: str | None,
) -> list[int]:
    rows = conn.execute(
        """
        SELECT DISTINCT asset_id
        FROM raw_document_assets
        WHERE raw_document_id = ?
        ORDER BY asset_id
        """,
        (filing_raw_document_id,),
    ).fetchall()
    assets = [int(r[0]) for r in rows]
    if assets:
        return assets
    if ticker:
        row = conn.execute(
            "SELECT asset_id FROM assets WHERE UPPER(ticker)=UPPER(?)",
            (ticker,),
        ).fetchone()
        if row is not None:
            return [int(row[0])]
    return []


def _event_observation_kind(
    conn: sqlite3.Connection,
    event_id: str,
    event_version_id: str,
) -> tuple[int, str | None, str]:
    previous = conn.execute(
        """
        SELECT event_observation_id, event_version_id, observation_sequence
        FROM normalized_event_observations
        WHERE event_id = ?
        ORDER BY observation_sequence DESC, created_at DESC
        LIMIT 1
        """,
        (event_id,),
    ).fetchone()
    if previous is None:
        return 1, None, "initial"

    prev_id, prev_version, prev_seq = str(previous[0]), str(previous[1]), int(previous[2])
    if prev_version == event_version_id:
        return prev_seq + 1, prev_id, "unchanged"

    existed_before = conn.execute(
        """
        SELECT 1
        FROM normalized_event_observations
        WHERE event_id = ?
          AND event_version_id = ?
        LIMIT 1
        """,
        (event_id, event_version_id),
    ).fetchone()
    kind = "reversion" if existed_before else "revision"
    return prev_seq + 1, prev_id, kind


def normalize(
    db: Path,
    clustering_run_id: str,
    *,
    normalization_run_id: str | None = None,
    as_of: str | None = None,
) -> dict[str, object]:
    if not db.is_file():
        raise FileNotFoundError(db)

    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.row_factory = sqlite3.Row
        ensure_contract(conn)
        c_run = _run_info(conn, clustering_run_id)
        cutoff = as_of or c_run["as_of"] or c_run["finished_at"]
        if not cutoff:
            raise ValueError("El clustering run no tiene as_of/finished_at")

        config = {
            "normalization_version": NORMALIZATION_VERSION,
            "taxonomy_version": TAXONOMY_VERSION,
            "semantic_schema_version": SEMANTIC_SCHEMA_VERSION,
            "source_adapter": "sec_edgar",
            "economic_direction_is_not_encoded": True,
            "source_reliability_is_not_encoded": True,
            "event_time_is_not_assumed_from_acceptance": True,
        }
        config_json = canonical_json(config)
        config_sha = hashlib.sha256(config_json.encode()).hexdigest()
        run_id = normalization_run_id or stable_id(
            "enr", clustering_run_id, NORMALIZATION_VERSION, cutoff
        )

        existing = conn.execute(
            """
            SELECT status, clusters_considered, events_observed,
                   evidence_semantics_written
            FROM event_normalization_runs
            WHERE normalization_run_id = ?
            """,
            (run_id,),
        ).fetchone()
        if existing is not None:
            if existing["status"] == "completed":
                return {
                    "normalization_run_id": run_id,
                    "status": "completed",
                    "rerun_reused": True,
                    "clusters_considered": int(existing["clusters_considered"]),
                    "events_observed": int(existing["events_observed"]),
                    "evidence_semantics_written": int(
                        existing["evidence_semantics_written"]
                    ),
                }
            raise RuntimeError(
                f"Normalization run existente no reutilizable: {run_id} "
                f"status={existing['status']}"
            )

        evidence = _sec_evidence(conn, clustering_run_id, str(cutoff))
        clusters = sorted({e.cluster_id for e in evidence})
        by_filing: dict[str, list[FilingEvidence]] = {}
        for row in evidence:
            by_filing.setdefault(row.filing_raw_document_id, []).append(row)

        started = utc_now()
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                """
                INSERT OR IGNORE INTO event_normalization_configs(
                    normalization_version, taxonomy_version,
                    semantic_schema_version, configuration_sha256,
                    configuration_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    NORMALIZATION_VERSION,
                    TAXONOMY_VERSION,
                    SEMANTIC_SCHEMA_VERSION,
                    config_sha,
                    config_json,
                ),
            )
            conn.execute(
                """
                INSERT INTO event_normalization_runs(
                    normalization_run_id, normalization_version,
                    clustering_run_id, started_at, status, as_of,
                    selection_json
                ) VALUES (?, ?, ?, ?, 'running', ?, ?)
                """,
                (
                    run_id,
                    NORMALIZATION_VERSION,
                    clustering_run_id,
                    started,
                    str(cutoff),
                    canonical_json({
                        "source": "sec",
                        "clustering_run_id": clustering_run_id,
                        "as_of": str(cutoff),
                    }),
                ),
            )

            event_observation_count = 0
            semantics_count = 0

            for filing_id, filing_evidence in sorted(by_filing.items()):
                metadata = _metadata_asof(conn, filing_id, str(cutoff))
                if metadata is None:
                    continue

                assets = _assets_for_filing(
                    conn,
                    filing_id,
                    metadata["ticker_at_ingestion"],
                )
                if not assets:
                    continue

                events = factual_events_for_filing(
                    str(metadata["form"]),
                    metadata["items_json"],
                )

                for event_spec in events:
                    identity_key = (
                        f"sec:{metadata['accession_number']}:"
                        f"{event_spec['identity_suffix']}"
                    )
                    event_id = stable_id("evt", identity_key)

                    normalized_payload = {
                        "source": "sec_edgar",
                        "accession_number": metadata["accession_number"],
                        "cik": metadata["cik"],
                        "form": metadata["form"],
                        "filing_date": metadata["filing_date"],
                        "acceptance_datetime": metadata["acceptance_datetime"],
                        "report_date": metadata["report_date"],
                        "item_identity": event_spec["identity_suffix"],
                        "event_type": event_spec["event_type"],
                        "event_subtype": event_spec["event_subtype"],
                        "is_amendment": int(metadata["is_amendment"]),
                        "entity_name": metadata["entity_name"],
                        "ticker_at_ingestion": metadata["ticker_at_ingestion"],
                        "metadata_observation_id": metadata["metadata_observation_id"],
                    }
                    payload_json = canonical_json(normalized_payload)
                    content_sha = hashlib.sha256(
                        payload_json.encode("utf-8")
                    ).hexdigest()
                    event_version_id = stable_id(
                        "evv", event_id, content_sha
                    )

                    conn.execute(
                        """
                        INSERT OR IGNORE INTO normalized_event_identities(
                            event_id, identity_method, identity_key, metadata_json
                        ) VALUES (?, 'sec_accession_item_v001', ?, ?)
                        """,
                        (
                            event_id,
                            identity_key,
                            canonical_json({
                                "source": "sec_edgar",
                                "accession_number": metadata["accession_number"],
                            }),
                        ),
                    )
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO normalized_event_versions(
                            event_version_id, event_id, event_type,
                            event_subtype, event_scope, canonical_title,
                            occurred_at, event_time_status, event_time_basis,
                            scheduled_for, resolved_status,
                            normalized_content_sha256, normalized_event_json,
                            parser_or_model_version, metadata_json
                        ) VALUES (
                            ?, ?, ?, ?, 'company', ?,
                            NULL, 'unknown',
                            'not_inferred_from_sec_acceptance',
                            NULL, 'observed', ?, ?, ?, ?
                        )
                        """,
                        (
                            event_version_id,
                            event_id,
                            event_spec["event_type"],
                            event_spec["event_subtype"],
                            (
                                f"{metadata['entity_name'] or metadata['ticker_at_ingestion'] or metadata['cik']} "
                                f"{event_spec['event_subtype']}"
                            ),
                            content_sha,
                            payload_json,
                            PARSER_VERSION,
                            canonical_json({
                                "underlying_occurrence_time_unknown": True,
                                "sec_acceptance_is_information_availability": True,
                            }),
                        ),
                    )

                    seq, prev_id, kind = _event_observation_kind(
                        conn, event_id, event_version_id
                    )
                    event_obs_id = stable_id(
                        "evo",
                        run_id,
                        event_id,
                        metadata["metadata_observation_id"],
                    )
                    conn.execute(
                        """
                        INSERT INTO normalized_event_observations(
                            event_observation_id, normalization_run_id,
                            event_id, event_version_id, observation_sequence,
                            previous_observation_id, observation_kind,
                            available_at, evidence_cutoff_at,
                            availability_is_point_in_time, metadata_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            event_obs_id,
                            run_id,
                            event_id,
                            event_version_id,
                            seq,
                            prev_id,
                            kind,
                            metadata["available_at"],
                            str(cutoff),
                            int(metadata["availability_is_point_in_time"]),
                            canonical_json({
                                "source_metadata_observation_id":
                                    metadata["metadata_observation_id"],
                                "metadata_observation_kind":
                                    metadata["observation_kind"],
                            }),
                        ),
                    )
                    event_observation_count += 1

                    # Link every SEC cluster supporting the filing.
                    cluster_first_membership: dict[str, FilingEvidence] = {}
                    for ev in filing_evidence:
                        cluster_first_membership.setdefault(ev.cluster_id, ev)

                    for cluster_index, (cluster_id, ev) in enumerate(
                        sorted(cluster_first_membership.items())
                    ):
                        link_role = (
                            "primary_evidence"
                            if cluster_index == 0
                            else "supporting_evidence"
                        )
                        link_id = stable_id(
                            "ecel", run_id, cluster_id, event_id, link_role
                        )
                        conn.execute(
                            """
                            INSERT OR IGNORE INTO event_cluster_event_links(
                                cluster_event_link_id, normalization_run_id,
                                clustering_run_id, cluster_id, event_id,
                                event_observation_id, link_role, linking_method,
                                available_at, point_in_time, metadata_json
                            ) VALUES (
                                ?, ?, ?, ?, ?, ?, ?, 'sec_accession_provenance',
                                ?, ?, ?
                            )
                            """,
                            (
                                link_id,
                                run_id,
                                clustering_run_id,
                                cluster_id,
                                event_id,
                                event_obs_id,
                                link_role,
                                ev.evidence_available_at,
                                ev.evidence_pit,
                                canonical_json({
                                    "filing_raw_document_id": filing_id,
                                }),
                            ),
                        )

                    # Evidence semantics are descriptive, not reliability scores.
                    for ev in filing_evidence:
                        semantic_id = stable_id(
                            "ees", run_id, ev.membership_id
                        )
                        inserted = conn.execute(
                            """
                            INSERT OR IGNORE INTO event_evidence_semantics(
                                evidence_semantic_id, normalization_run_id,
                                membership_id, semantic_type, semantic_method,
                                semantic_model_version,
                                classification_confidence, available_at,
                                point_in_time, semantic_json, metadata_json
                            ) VALUES (
                                ?, ?, ?, 'official_statement',
                                'deterministic_metadata', ?,
                                1.0, ?, ?, ?, ?
                            )
                            """,
                            (
                                semantic_id,
                                run_id,
                                ev.membership_id,
                                PARSER_VERSION,
                                ev.evidence_available_at,
                                ev.evidence_pit,
                                canonical_json({
                                    "source": "sec_edgar",
                                    "meaning":
                                        "official filing evidence; no market "
                                        "direction or truth-of-claim score implied",
                                }),
                                canonical_json({
                                    "filing_raw_document_id": filing_id,
                                }),
                            ),
                        ).rowcount
                        semantics_count += int(inserted == 1)

                    for asset_id in assets:
                        asset_link_id = stable_id(
                            "eal", run_id, event_obs_id, asset_id, "issuer_asset"
                        )
                        conn.execute(
                            """
                            INSERT OR IGNORE INTO normalized_event_asset_links(
                                event_asset_link_id, normalization_run_id,
                                event_observation_id, event_id, asset_id,
                                asset_role, linking_method,
                                extraction_confidence, available_at,
                                point_in_time, metadata_json
                            ) VALUES (
                                ?, ?, ?, ?, ?, 'issuer_asset',
                                'deterministic_sec_issuer', 1.0, ?, ?, ?
                            )
                            """,
                            (
                                asset_link_id,
                                run_id,
                                event_obs_id,
                                event_id,
                                asset_id,
                                metadata["available_at"],
                                int(metadata["availability_is_point_in_time"]),
                                canonical_json({
                                    "filing_raw_document_id": filing_id,
                                }),
                            ),
                        )

                        entity = conn.execute(
                            """
                            SELECT entity_id
                            FROM asset_entities
                            WHERE asset_id = ?
                            """,
                            (asset_id,),
                        ).fetchone()
                        if entity is not None:
                            entity_id = int(entity[0])
                            entity_link_id = stable_id(
                                "eel", run_id, event_obs_id, entity_id, "issuer"
                            )
                            conn.execute(
                                """
                                INSERT OR IGNORE INTO normalized_event_entity_links(
                                    event_entity_link_id, normalization_run_id,
                                    event_observation_id, event_id, entity_id,
                                    entity_role, linking_method,
                                    extraction_confidence, available_at,
                                    point_in_time, metadata_json
                                ) VALUES (
                                    ?, ?, ?, ?, ?, 'issuer',
                                    'deterministic_metadata', 1.0, ?, ?, ?
                                )
                                """,
                                (
                                    entity_link_id,
                                    run_id,
                                    event_obs_id,
                                    event_id,
                                    entity_id,
                                    metadata["available_at"],
                                    int(
                                        metadata["availability_is_point_in_time"]
                                    ),
                                    canonical_json({
                                        "asset_id": asset_id,
                                    }),
                                ),
                            )

            finished = utc_now()
            conn.execute(
                """
                UPDATE event_normalization_runs
                SET finished_at = ?,
                    status = 'completed',
                    clusters_considered = ?,
                    events_observed = ?,
                    evidence_semantics_written = ?
                WHERE normalization_run_id = ?
                """,
                (
                    finished,
                    len(clusters),
                    event_observation_count,
                    semantics_count,
                    run_id,
                ),
            )
            conn.commit()
        except Exception as error:
            conn.rollback()
            # Persist failed run separately when possible.
            try:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO event_normalization_runs(
                        normalization_run_id, normalization_version,
                        clustering_run_id, started_at, finished_at, status,
                        as_of, selection_json, error_json
                    ) VALUES (?, ?, ?, ?, ?, 'failed', ?, ?, ?)
                    """,
                    (
                        run_id,
                        NORMALIZATION_VERSION,
                        clustering_run_id,
                        started,
                        utc_now(),
                        str(cutoff),
                        canonical_json({"source": "sec"}),
                        canonical_json({
                            "type": type(error).__name__,
                            "message": str(error),
                        }),
                    ),
                )
                conn.commit()
            except Exception:
                conn.rollback()
            raise

    return {
        "normalization_run_id": run_id,
        "status": "completed",
        "rerun_reused": False,
        "clusters_considered": len(clusters),
        "filings_considered": len(by_filing),
        "events_observed": event_observation_count,
        "evidence_semantics_written": semantics_count,
    }


def main() -> None:
    ap = argparse.ArgumentParser(
        description="SEC -> normalized factual events v0.1"
    )
    ap.add_argument("--clustering-run-id", required=True)
    ap.add_argument("--normalization-run-id")
    ap.add_argument("--as-of")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = ap.parse_args()
    result = normalize(
        args.db,
        args.clustering_run_id,
        normalization_run_id=args.normalization_run_id,
        as_of=args.as_of,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
