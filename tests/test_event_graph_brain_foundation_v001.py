from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from knowledge.entities.seed_asset_entity_proxies_v001 import (
    seed_asset_entity_proxies,
)
from ingestion.events.direct_event_entity_bridge_v001 import (
    bridge_direct_events,
)
from knowledge.graph.temporal_graph_v001 import (
    add_relation_observation,
    promote_structural_relation,
    structural_relations_asof,
)
from knowledge.graph.propagation_candidates_v001 import (
    generate_for_event,
)


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (
    ROOT / "database/migrations/020_event_graph_brain_foundation.sql"
)


def base_db(tmp_path: Path) -> Path:
    db = tmp_path / "db.sqlite"
    with sqlite3.connect(db) as conn:
        conn.executescript("""
        PRAGMA foreign_keys=ON;
        CREATE TABLE schema_migrations(
          version TEXT PRIMARY KEY,
          name TEXT NOT NULL
        );
        INSERT INTO schema_migrations(version,name)
        VALUES ('019','event_brain_v001');
        CREATE TABLE assets(
          asset_id INTEGER PRIMARY KEY AUTOINCREMENT,
          ticker TEXT NOT NULL UNIQUE,
          name TEXT,
          asset_type TEXT NOT NULL DEFAULT 'equity',
          sector TEXT,
          active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE entities(
          entity_id INTEGER PRIMARY KEY AUTOINCREMENT,
          entity_type TEXT NOT NULL,
          canonical_name TEXT NOT NULL,
          external_id TEXT,
          country TEXT,
          metadata_json TEXT,
          UNIQUE(entity_type, canonical_name)
        );
        CREATE TABLE asset_entities(
          asset_id INTEGER PRIMARY KEY,
          entity_id INTEGER NOT NULL,
          FOREIGN KEY(asset_id) REFERENCES assets(asset_id),
          FOREIGN KEY(entity_id) REFERENCES entities(entity_id)
        );
        CREATE TABLE relation_types(
          relation_type TEXT PRIMARY KEY,
          description TEXT,
          signed INTEGER NOT NULL DEFAULT 0
        );
        INSERT INTO relation_types VALUES
          ('supplier_of','supplier',0),
          ('competitor_of','competitor',1),
          ('regulated_by','regulator',1),
          ('exposed_to','exposure',1),
          ('owns','ownership',0);
        CREATE TABLE normalized_event_state_snapshots(
          event_state_id TEXT PRIMARY KEY,
          event_id TEXT NOT NULL,
          asset_id INTEGER NOT NULL,
          state_time TEXT NOT NULL,
          feature_version TEXT NOT NULL,
          point_in_time_evidence_fraction REAL NOT NULL
        );
        """)
        conn.executescript(MIGRATION.read_text(encoding="utf-8"))
        conn.executemany(
            """
            INSERT INTO assets(
              asset_id,ticker,name,asset_type,sector,active
            ) VALUES (?,?,?,?,?,?)
            """,
            [
                (1,"AAA","Alpha Inc","equity","Tech",1),
                (2,"BBB","Beta Inc","equity","Tech",1),
                (3,"ETF","ETF Ref","etf_reference",None,0),
            ],
        )
        conn.executemany(
            """
            INSERT INTO normalized_event_state_snapshots VALUES
            (?,?,?,?,?,?)
            """,
            [
                (
                    "s1","event-a",1,"2024-01-02T15:00:00+00:00",
                    "event_state_v002",1.0
                ),
                (
                    "s2","event-a",1,"2024-01-02T16:00:00+00:00",
                    "event_state_v002",1.0
                ),
                (
                    "s3","event-b",2,"2024-01-03T15:00:00+00:00",
                    "event_state_v002",0.0
                ),
            ],
        )
        conn.commit()
    return db


def config():
    return json.loads(
        (ROOT / "config/event_graph_brain_foundation_v001.json")
        .read_text(encoding="utf-8")
    )


def test_contract_freezes_v004_and_one_hop():
    c = config()
    assert c["market_prior"]["do_not_tune_in_this_stage"] is True
    assert c["graph_contract"]["foundation_max_hops"] == 1
    assert c["graph_contract"]["primary_layer"] == "structural"
    assert c["graph_contract"]["relation_sign_hardcoded"] is False
    assert c["graph_contract"]["relation_weight_hardcoded"] is False


def test_migration_has_no_market_direction_in_model_visible_graph():
    sql = MIGRATION.read_text(encoding="utf-8").lower()
    # The phrase can occur in comments, but the factual table DDL section must
    # not define expected_direction / predictive weight columns.
    with sqlite3.connect(":memory:") as conn:
        conn.executescript("""
        CREATE TABLE schema_migrations(
          version TEXT PRIMARY KEY,name TEXT
        );
        CREATE TABLE assets(asset_id INTEGER PRIMARY KEY);
        CREATE TABLE entities(
          entity_id INTEGER PRIMARY KEY,
          entity_type TEXT,canonical_name TEXT,external_id TEXT,
          country TEXT,metadata_json TEXT,
          UNIQUE(entity_type,canonical_name)
        );
        CREATE TABLE relation_types(
          relation_type TEXT PRIMARY KEY,
          description TEXT,
          signed INTEGER
        );
        """)
        conn.executescript(MIGRATION.read_text(encoding="utf-8"))
        for table in (
            "event_entity_links_v001",
            "temporal_relation_assertions_v001",
            "temporal_relation_observations_v001",
            "graph_propagation_candidates_v001",
        ):
            cols = {
                row[1] for row in conn.execute(
                    f"PRAGMA table_info({table})"
                )
            }
            assert "expected_direction" not in cols
            assert "market_impact" not in cols
            assert "predictive_weight" not in cols
            assert "edge_weight" not in cols


def test_asset_proxy_seed_excludes_inactive_reference_asset(tmp_path):
    db = base_db(tmp_path)
    out = seed_asset_entity_proxies(db)
    assert out["status"] == "PASS"
    assert out["eligible_assets"] == 2
    assert out["asset_entity_links_created"] == 2
    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM asset_entities"
        ).fetchone()[0] == 2
        assert conn.execute(
            """
            SELECT COUNT(*) FROM asset_entities ae
            JOIN assets a ON a.asset_id=ae.asset_id
            WHERE a.ticker='ETF'
            """
        ).fetchone()[0] == 0


def test_proxy_seed_is_idempotent(tmp_path):
    db = base_db(tmp_path)
    first = seed_asset_entity_proxies(db)
    second = seed_asset_entity_proxies(db)
    assert first["asset_entity_links_created"] == 2
    assert second["asset_entity_links_created"] == 0
    assert second["existing_mappings"] == 2


def test_direct_event_bridge_uses_first_state_time(tmp_path):
    db = base_db(tmp_path)
    seed_asset_entity_proxies(db)
    out = bridge_direct_events(db)
    assert out["status"] == "PASS"
    assert out["events"] == 2
    with sqlite3.connect(db) as conn:
        row = conn.execute(
            """
            SELECT first_available_at,availability_is_point_in_time
            FROM event_entity_links_v001
            WHERE event_id='event-a'
            """
        ).fetchone()
    assert row[0] == "2024-01-02T15:00:00+00:00"
    assert row[1] == 1


def test_non_pit_event_bridge_stays_non_pit(tmp_path):
    db = base_db(tmp_path)
    seed_asset_entity_proxies(db)
    bridge_direct_events(db)
    with sqlite3.connect(db) as conn:
        pit = conn.execute(
            """
            SELECT availability_is_point_in_time
            FROM event_entity_links_v001
            WHERE event_id='event-b'
            """
        ).fetchone()[0]
    assert pit == 0


def test_future_relation_evidence_is_not_visible(tmp_path):
    db = base_db(tmp_path)
    seed_asset_entity_proxies(db)
    with sqlite3.connect(db) as conn:
        a = conn.execute(
            "SELECT entity_id FROM asset_entities WHERE asset_id=1"
        ).fetchone()[0]
        b = conn.execute(
            "SELECT entity_id FROM asset_entities WHERE asset_id=2"
        ).fetchone()[0]
        promote_structural_relation(
            conn,
            source_entity_id=a,
            target_entity_id=b,
            relation_type="supplier_of",
            evidence_available_at="2024-02-01T00:00:00+00:00",
            evidence_type="filing",
            source_ref="doc-1",
            assertion_version="rel-v1",
            availability_basis="test",
            availability_is_point_in_time=1,
        )
        conn.commit()
        before = structural_relations_asof(
            conn, "2024-01-15T00:00:00+00:00"
        )
        after = structural_relations_asof(
            conn, "2024-02-02T00:00:00+00:00"
        )
    assert before == []
    assert len(after) == 1


def test_relation_retraction_is_temporal_not_history_rewrite(tmp_path):
    db = base_db(tmp_path)
    seed_asset_entity_proxies(db)
    with sqlite3.connect(db) as conn:
        a = conn.execute(
            "SELECT entity_id FROM asset_entities WHERE asset_id=1"
        ).fetchone()[0]
        b = conn.execute(
            "SELECT entity_id FROM asset_entities WHERE asset_id=2"
        ).fetchone()[0]
        rid, _ = promote_structural_relation(
            conn,
            source_entity_id=a,
            target_entity_id=b,
            relation_type="supplier_of",
            evidence_available_at="2024-01-01T00:00:00+00:00",
            evidence_type="filing",
            source_ref="doc-1",
            assertion_version="rel-v1",
            availability_basis="test",
            availability_is_point_in_time=1,
        )
        add_relation_observation(
            conn,
            relation_assertion_id=rid,
            observation_action="retracted",
            evidence_available_at="2024-03-01T00:00:00+00:00",
            evidence_type="filing",
            source_ref="doc-2",
            availability_basis="test",
            availability_is_point_in_time=1,
        )
        conn.commit()
        feb = structural_relations_asof(
            conn, "2024-02-01T00:00:00+00:00"
        )
        mar = structural_relations_asof(
            conn, "2024-03-02T00:00:00+00:00"
        )
    assert len(feb) == 1
    assert mar == []


def test_one_hop_propagation_discovers_connected_asset_without_sign(tmp_path):
    db = base_db(tmp_path)
    seed_asset_entity_proxies(db)
    bridge_direct_events(db)
    with sqlite3.connect(db) as conn:
        a = conn.execute(
            "SELECT entity_id FROM asset_entities WHERE asset_id=1"
        ).fetchone()[0]
        b = conn.execute(
            "SELECT entity_id FROM asset_entities WHERE asset_id=2"
        ).fetchone()[0]
        promote_structural_relation(
            conn,
            source_entity_id=b,
            target_entity_id=a,
            relation_type="supplier_of",
            evidence_available_at="2023-12-01T00:00:00+00:00",
            evidence_type="filing",
            source_ref="doc-1",
            assertion_version="rel-v1",
            availability_basis="test",
            availability_is_point_in_time=1,
        )
        conn.commit()
        candidates = generate_for_event(
            conn,
            event_id="event-a",
            as_of="2024-01-02T15:00:00+00:00",
            allowed_relation_types={"supplier_of"},
        )
    direct = [x for x in candidates if x["exposure_kind"] == "direct"]
    graph = [x for x in candidates if x["exposure_kind"] == "graph"]
    assert {x["target_asset_id"] for x in direct} == {1}
    assert {x["target_asset_id"] for x in graph} == {2}
    assert graph[0]["path_edge_orientations"] == ["incoming"]
    assert graph[0]["metadata"]["market_direction_assigned"] is False
    assert graph[0]["metadata"]["market_weight_assigned"] is False


def test_future_graph_edge_cannot_propagate(tmp_path):
    db = base_db(tmp_path)
    seed_asset_entity_proxies(db)
    bridge_direct_events(db)
    with sqlite3.connect(db) as conn:
        a = conn.execute(
            "SELECT entity_id FROM asset_entities WHERE asset_id=1"
        ).fetchone()[0]
        b = conn.execute(
            "SELECT entity_id FROM asset_entities WHERE asset_id=2"
        ).fetchone()[0]
        promote_structural_relation(
            conn,
            source_entity_id=a,
            target_entity_id=b,
            relation_type="supplier_of",
            evidence_available_at="2024-02-01T00:00:00+00:00",
            evidence_type="filing",
            source_ref="future-doc",
            assertion_version="rel-v1",
            availability_basis="test",
            availability_is_point_in_time=1,
        )
        conn.commit()
        candidates = generate_for_event(
            conn,
            event_id="event-a",
            as_of="2024-01-02T15:00:00+00:00",
            allowed_relation_types={"supplier_of"},
        )
    assert all(x["exposure_kind"] == "direct" for x in candidates)


def test_foundation_rejects_deeper_hops(tmp_path):
    db = base_db(tmp_path)
    seed_asset_entity_proxies(db)
    bridge_direct_events(db)
    with sqlite3.connect(db) as conn:
        try:
            generate_for_event(
                conn,
                event_id="event-a",
                as_of="2024-01-02T15:00:00+00:00",
                max_hops=2,
            )
        except ValueError as exc:
            assert "one graph hop" in str(exc)
        else:
            raise AssertionError("2-hop propagation should be rejected")


def test_evaluation_ladder_requires_direct_event_before_graph():
    c = config()
    stages = [x["stage"] for x in c["evaluation_ladder"]]
    assert stages == ["D1", "E0", "E1"]
    assert c["evaluation_ladder"][2]["control"] == (
        "V004 + direct event"
    )


def test_cooccurrence_does_not_imply_relation():
    c = config()
    assert c["candidate_promotion_contract"][
        "cooccurrence_does_not_imply_relation"
    ] is True


def test_gnn_and_learned_graph_are_deferred():
    c = config()
    assert "gnn" in c["graph_contract"]["deferred_methods"]
    assert "learned" in c["graph_contract"]["deferred_layers"]
    assert "statistical" in c["graph_contract"]["deferred_layers"]


def test_direct_event_bridge_rerun_preserves_link_provenance(tmp_path):
    db = base_db(tmp_path)
    seed_asset_entity_proxies(db)
    first = bridge_direct_events(db)
    second = bridge_direct_events(db)
    assert first["status"] == "PASS"
    assert second["status"] == "PASS"
    assert first["link_run_id"] == second["link_run_id"]
    with sqlite3.connect(db) as conn:
        rows = conn.execute(
            """
            SELECT COUNT(*),
                   SUM(CASE WHEN link_run_id IS NULL THEN 1 ELSE 0 END)
            FROM event_entity_links_v001
            """
        ).fetchone()
        run_rows = conn.execute(
            "SELECT COUNT(*) FROM event_entity_link_runs_v001"
        ).fetchone()[0]
    assert rows[0] == 2
    assert rows[1] == 0
    assert run_rows == 1
    assert second["links_present_for_run"] == 2


def test_relation_promotion_same_evidence_is_idempotent(tmp_path):
    db = base_db(tmp_path)
    seed_asset_entity_proxies(db)
    with sqlite3.connect(db) as conn:
        a = conn.execute(
            "SELECT entity_id FROM asset_entities WHERE asset_id=1"
        ).fetchone()[0]
        b = conn.execute(
            "SELECT entity_id FROM asset_entities WHERE asset_id=2"
        ).fetchone()[0]
        first = promote_structural_relation(
            conn,
            source_entity_id=a,
            target_entity_id=b,
            relation_type="supplier_of",
            evidence_available_at="2024-01-01T00:00:00+00:00",
            evidence_type="filing",
            source_ref="doc-1",
            assertion_version="rel-v1",
            availability_basis="test",
            availability_is_point_in_time=1,
            evidence_sha256="abc",
        )
        second = promote_structural_relation(
            conn,
            source_entity_id=a,
            target_entity_id=b,
            relation_type="supplier_of",
            evidence_available_at="2024-01-01T00:00:00+00:00",
            evidence_type="filing",
            source_ref="doc-1",
            assertion_version="rel-v1",
            availability_basis="test",
            availability_is_point_in_time=1,
            evidence_sha256="abc",
        )
        conn.commit()
        n = conn.execute(
            "SELECT COUNT(*) FROM temporal_relation_observations_v001"
        ).fetchone()[0]
    assert first == second
    assert n == 1
