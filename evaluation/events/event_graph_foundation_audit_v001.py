from __future__ import annotations

import json
import sqlite3
from pathlib import Path


REQUIRED_TABLES = {
    "event_entity_link_runs_v001",
    "event_entity_links_v001",
    "event_entity_candidates_v001",
    "graph_relation_candidates_v001",
    "temporal_relation_assertions_v001",
    "temporal_relation_observations_v001",
    "event_semantic_inference_runs_v001",
    "event_semantic_inferences_v001",
    "graph_propagation_runs_v001",
    "graph_propagation_candidates_v001",
}

FORBIDDEN_MARKET_SEMANTIC_COLUMNS = {
    "expected_direction",
    "market_direction",
    "impact",
    "market_impact",
    "predictive_weight",
    "edge_weight",
    "decay",
}


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {
        str(row[1])
        for row in conn.execute(f"PRAGMA table_info({table})")
    }


def audit(db: Path) -> dict:
    failures: list[str] = []
    reviews: list[str] = []

    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        tables = _tables(conn)

        missing_tables = sorted(REQUIRED_TABLES - tables)
        if missing_tables:
            failures.extend(
                f"missing_table:{x}" for x in missing_tables
            )

        migration = conn.execute(
            """
            SELECT name FROM schema_migrations WHERE version='019'
            """
        ).fetchone()
        if migration is None:
            failures.append("migration_019_not_registered")
        elif str(migration[0]) != "event_graph_brain_foundation":
            failures.append(
                f"migration_019_name_conflict:{migration[0]}"
            )

        eligible_assets = conn.execute(
            """
            SELECT COUNT(*) FROM assets
            WHERE asset_type='equity' AND active=1
            """
        ).fetchone()[0]
        mapped_assets = conn.execute(
            """
            SELECT COUNT(*)
            FROM assets a
            JOIN asset_entities ae ON ae.asset_id=a.asset_id
            WHERE a.asset_type='equity' AND a.active=1
            """
        ).fetchone()[0]
        proxy_entities = conn.execute(
            """
            SELECT COUNT(*) FROM entities
            WHERE entity_type='listed_asset_proxy'
            """
        ).fetchone()[0]

        event_source_present = (
            "normalized_event_state_snapshots" in tables
        )
        if event_source_present:
            event_cols = _columns(
                conn, "normalized_event_state_snapshots"
            )
            required_event_cols = {
                "event_id","asset_id","state_time","feature_version"
            }
            if not required_event_cols.issubset(event_cols):
                failures.append(
                    "normalized_event_state_snapshot_contract_missing"
                )
            normalized_events = conn.execute(
                """
                SELECT COUNT(DISTINCT event_id)
                FROM normalized_event_state_snapshots
                WHERE feature_version='event_state_v002'
                """
            ).fetchone()[0]
            normalized_event_assets = conn.execute(
                """
                SELECT COUNT(DISTINCT asset_id)
                FROM normalized_event_state_snapshots
                WHERE feature_version='event_state_v002'
                """
            ).fetchone()[0]
        else:
            normalized_events = 0
            normalized_event_assets = 0
            reviews.append(
                "normalized_event_state_snapshots_missing"
            )

        direct_links = (
            conn.execute(
                "SELECT COUNT(*) FROM event_entity_links_v001"
            ).fetchone()[0]
            if "event_entity_links_v001" in tables
            else 0
        )
        direct_link_events = (
            conn.execute(
                """
                SELECT COUNT(DISTINCT event_id)
                FROM event_entity_links_v001
                """
            ).fetchone()[0]
            if "event_entity_links_v001" in tables
            else 0
        )
        direct_link_assets = (
            conn.execute(
                """
                SELECT COUNT(DISTINCT asset_id)
                FROM event_entity_links_v001
                WHERE asset_id IS NOT NULL
                """
            ).fetchone()[0]
            if "event_entity_links_v001" in tables
            else 0
        )
        direct_link_missing_entity = (
            conn.execute(
                """
                SELECT COUNT(*)
                FROM event_entity_links_v001 l
                LEFT JOIN entities e ON e.entity_id=l.entity_id
                WHERE e.entity_id IS NULL
                """
            ).fetchone()[0]
            if "event_entity_links_v001" in tables
            else 0
        )
        if direct_link_missing_entity:
            failures.append("direct_link_missing_entity")

        relation_assertions = (
            conn.execute(
                """
                SELECT COUNT(*)
                FROM temporal_relation_assertions_v001
                """
            ).fetchone()[0]
            if "temporal_relation_assertions_v001" in tables
            else 0
        )
        relation_observations = (
            conn.execute(
                """
                SELECT COUNT(*)
                FROM temporal_relation_observations_v001
                """
            ).fetchone()[0]
            if "temporal_relation_observations_v001" in tables
            else 0
        )
        pending_relation_candidates = (
            conn.execute(
                """
                SELECT COUNT(*)
                FROM graph_relation_candidates_v001
                WHERE status='pending'
                """
            ).fetchone()[0]
            if "graph_relation_candidates_v001" in tables
            else 0
        )

        # Schema anti-shortcut check: factual graph/model-visible tables must
        # not contain hardcoded market impact fields.
        for table in (
            "event_entity_links_v001",
            "temporal_relation_assertions_v001",
            "temporal_relation_observations_v001",
            "graph_propagation_candidates_v001",
        ):
            if table not in tables:
                continue
            bad = sorted(
                _columns(conn, table)
                & FORBIDDEN_MARKET_SEMANTIC_COLUMNS
            )
            if bad:
                failures.append(
                    f"{table}_forbidden_market_columns:{','.join(bad)}"
                )

        dangling_assertions = (
            conn.execute(
                """
                SELECT COUNT(*)
                FROM temporal_relation_assertions_v001 a
                LEFT JOIN temporal_relation_observations_v001 o
                  ON o.relation_assertion_id=a.relation_assertion_id
                WHERE o.relation_observation_id IS NULL
                """
            ).fetchone()[0]
            if "temporal_relation_assertions_v001" in tables
            else 0
        )
        if dangling_assertions:
            failures.append(
                f"relation_assertions_without_observation={dangling_assertions}"
            )

        if int(mapped_assets) != int(eligible_assets):
            failures.append(
                "active_equity_asset_entity_coverage_incomplete"
            )

        # Zero graph relations is acceptable now: this is a foundation audit.
        if relation_assertions == 0:
            reviews.append(
                "structural_graph_empty_expected_before_relation_ingestion"
            )

        status = (
            "FAIL"
            if failures
            else ("REVIEW" if reviews else "PASS")
        )

    return {
        "status": status,
        "failures": sorted(set(failures)),
        "reviews": sorted(set(reviews)),
        "asset_entity_coverage": {
            "eligible_active_equities": int(eligible_assets),
            "mapped_active_equities": int(mapped_assets),
            "coverage_fraction": (
                float(mapped_assets / eligible_assets)
                if eligible_assets else 0.0
            ),
            "listed_asset_proxy_entities": int(proxy_entities),
        },
        "existing_event_bridge": {
            "normalized_event_source_present": event_source_present,
            "normalized_events_v002": int(normalized_events),
            "normalized_event_assets_v002": int(normalized_event_assets),
            "direct_entity_links": int(direct_links),
            "direct_link_events": int(direct_link_events),
            "direct_link_assets": int(direct_link_assets),
        },
        "temporal_structural_graph": {
            "validated_relation_assertions": int(relation_assertions),
            "relation_observations": int(relation_observations),
            "pending_relation_candidates": int(
                pending_relation_candidates
            ),
            "assertions_without_observation": int(dangling_assertions),
        },
        "hard_contracts": {
            "event_evidence_separate_from_semantic_inference": True,
            "relation_candidates_not_model_visible": True,
            "relation_availability_is_causal_gate": True,
            "graph_primary_layer": "structural",
            "foundation_max_hops": 1,
            "hardcoded_market_direction": False,
            "hardcoded_relation_weight": False,
            "hardcoded_event_decay": False,
            "cooccurrence_is_not_relation": True,
            "gnn_enabled": False,
        },
        "next_gate": (
            "After PASS/expected REVIEW: populate and audit historical "
            "structural relation evidence. Do not train graph propagation "
            "models yet."
        ),
    }
