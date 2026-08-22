from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from evaluation.diagnostics.validate_outcomes import validate


def test_outcome_invariants_on_db(tmp_path: Path):
    db = tmp_path / "test.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE realized_outcomes (
            outcome_id INTEGER PRIMARY KEY,
            asset_id INTEGER NOT NULL,
            origin_time TEXT NOT NULL,
            horizon_seconds INTEGER NOT NULL,
            end_time TEXT,
            start_price REAL,
            end_price REAL,
            return_pct REAL,
            mfe_pct REAL,
            mae_pct REAL,
            min_price REAL,
            max_price REAL,
            realized_volatility REAL,
            path_json TEXT,
            source TEXT,
            target_version TEXT NOT NULL,
            created_at TEXT,
            observed_bars INTEGER,
            expected_bars INTEGER,
            coverage_pct REAL,
            max_gap_seconds REAL,
            session_count INTEGER,
            data_quality TEXT,
            quality_version TEXT
        );
        INSERT INTO realized_outcomes VALUES
        (1,1,'2026-01-01T10:00:00Z',300,'2026-01-01T10:05:00Z',100,101,1,2,-1,99,102,0.1,'[]','test','v0.1','now',5,5,100,0,1,'good','v0.1');
        """
    )
    conn.commit()
    conn.close()

    report = validate(db)
    assert report["status"] == "PASS"
    assert report["failures_count"] == 0
