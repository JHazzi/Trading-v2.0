from pathlib import Path
import sqlite3

from models.market.dataset import FEATURES, load_supervised_dataset


def test_loader_pivots_feature_snapshots(tmp_path: Path):
    db = tmp_path / "t.db"
    with sqlite3.connect(db) as conn:
        conn.executescript("""
        CREATE TABLE realized_outcomes (
            outcome_id INTEGER PRIMARY KEY,
            asset_id INTEGER, origin_time TEXT, horizon_seconds INTEGER,
            return_pct REAL, mfe_pct REAL, mae_pct REAL, coverage_pct REAL,
            data_quality TEXT
        );
        CREATE TABLE feature_snapshots (
            feature_snapshot_id INTEGER PRIMARY KEY,
            asset_id INTEGER, timestamp TEXT, feature_name TEXT,
            feature_value REAL, feature_version TEXT
        );
        """)
        conn.execute("INSERT INTO realized_outcomes VALUES (1,1,'2026-01-01T10:00:00+00:00',300,1.0,2.0,-1.0,100,'good')")
        for i, feature in enumerate(FEATURES, 1):
            conn.execute("INSERT INTO feature_snapshots VALUES (?,?,?,?,?,?)", (i,1,'2026-01-01T10:00:00+00:00',feature,float(i),'market_state_v0.1.0'))
        conn.commit()
    df = load_supervised_dataset(db, 300)
    assert len(df) == 1
    assert df.iloc[0][FEATURES[-1]] == float(len(FEATURES))
