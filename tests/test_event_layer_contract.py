import sqlite3
from pathlib import Path

from evaluation.diagnostics.validate_event_causality import validate
from features.news.causal_event_features import build_event_snapshot


SCHEMA = """
CREATE TABLE assets (
    asset_id INTEGER PRIMARY KEY
);
CREATE TABLE news_documents (
    news_id TEXT PRIMARY KEY
);
CREATE TABLE events (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    canonical_title TEXT,
    first_seen_at TEXT,
    last_seen_at TEXT,
    event_time TEXT,
    status TEXT,
    event_scope TEXT,
    metadata_json TEXT
);
CREATE TABLE event_assets (
    event_id TEXT NOT NULL,
    asset_id INTEGER NOT NULL,
    relevance REAL,
    expected_direction REAL,
    PRIMARY KEY(event_id, asset_id)
);
CREATE TABLE event_clusters (
    cluster_id TEXT PRIMARY KEY
);
CREATE TABLE event_cluster_news (
    cluster_id TEXT NOT NULL,
    news_id TEXT NOT NULL,
    PRIMARY KEY(cluster_id, news_id)
);
CREATE TABLE event_evidence (
    evidence_id INTEGER PRIMARY KEY,
    event_id TEXT NOT NULL,
    news_id TEXT,
    available_at TEXT NOT NULL,
    evidence_type TEXT NOT NULL
);
CREATE TABLE event_states (
    event_state_id INTEGER PRIMARY KEY,
    event_id TEXT NOT NULL,
    asset_id INTEGER,
    state_time TEXT NOT NULL,
    available_at TEXT NOT NULL,
    novelty REAL,
    evidence_count INTEGER DEFAULT 0,
    source_diversity REAL,
    uncertainty REAL,
    expected_surprise REAL,
    expected_direction REAL,
    event_persistence REAL,
    event_age_seconds REAL,
    future_event_flag INTEGER NOT NULL DEFAULT 0,
    feature_version TEXT NOT NULL,
    metadata_json TEXT
);
CREATE TABLE event_reaction_outcomes (
    reaction_id INTEGER PRIMARY KEY
);
CREATE TABLE event_source_knowledge (
    knowledge_id INTEGER PRIMARY KEY
);
"""


def create_db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA)
        conn.execute("INSERT INTO assets(asset_id) VALUES (1)")


def test_event_migration_010_exists():
    assert Path("database/migrations/010_event_layer.sql").exists()


def test_empty_event_layer_is_incomplete(tmp_path):
    db = tmp_path / "events.db"
    create_db(db)

    result = validate(db)

    assert result["status"] == "INCOMPLETE"
    assert result["causal_violations"] == 0
    assert result["feature_smoke_test_ready"] is False
    assert result["training_ready"] is False


def test_event_snapshot_uses_latest_state_available_as_of(tmp_path):
    db = tmp_path / "events.db"
    create_db(db)

    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            INSERT INTO events(
                event_id, event_type, canonical_title, status, event_scope
            )
            VALUES ('e1', 'earnings', 'Example earnings', 'active', 'company')
            """
        )
        conn.execute(
            """
            INSERT INTO event_assets(
                event_id, asset_id, relevance, expected_direction
            )
            VALUES ('e1', 1, 0.8, 0.2)
            """
        )
        conn.executemany(
            """
            INSERT INTO event_states(
                event_id, asset_id, state_time, available_at,
                novelty, evidence_count, uncertainty,
                expected_direction, feature_version
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "e1",
                    1,
                    "2026-08-20T10:00:00+00:00",
                    "2026-08-20T09:59:00+00:00",
                    0.3,
                    1,
                    0.7,
                    None,
                    "event_state_v0.1",
                ),
                (
                    "e1",
                    1,
                    "2026-08-20T12:00:00+00:00",
                    "2026-08-20T11:59:00+00:00",
                    0.5,
                    2,
                    0.4,
                    0.6,
                    "event_state_v0.1",
                ),
                (
                    "e1",
                    1,
                    "2026-08-20T15:00:00+00:00",
                    "2026-08-20T14:59:00+00:00",
                    0.9,
                    3,
                    0.2,
                    0.9,
                    "event_state_v0.1",
                ),
            ],
        )

    result = build_event_snapshot(
        db,
        asset_id=1,
        as_of="2026-08-20T13:00:00+00:00",
    )

    assert result["event_count"] == 1
    assert result["events"][0]["state_time"] == (
        "2026-08-20T12:00:00+00:00"
    )
    assert result["events"][0]["expected_direction"] == 0.6
    assert result["events"][0]["available_at"] <= result["as_of"]


def test_event_brain_does_not_hardcode_decay_or_impact():
    text = Path(
        "features/news/causal_event_features.py"
    ).read_text(encoding="utf-8")

    assert "exp(-" not in text
    assert "impact =" not in text

