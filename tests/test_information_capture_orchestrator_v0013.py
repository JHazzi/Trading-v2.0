from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from research.information_sources.orchestrator_v0013 import (
    apply_schema, requests_in_window, coverage_audit
)


def test_schema_is_additive(tmp_path):
    db=tmp_path/"x.db"
    c=sqlite3.connect(db)
    c.executescript("""
    CREATE TABLE source_observations (
      observation_id TEXT PRIMARY KEY, source_name TEXT, source_ref TEXT,
      retrieved_at TEXT, available_at TEXT, strict_pit INTEGER, raw_payload_json TEXT, metadata_json TEXT
    );
    CREATE TABLE expectation_observations (
      observation_id TEXT PRIMARY KEY, source_observation_id TEXT, asset_ticker TEXT,
      available_at TEXT, metric_key TEXT, statistic_key TEXT, fiscal_period TEXT, metadata_json TEXT
    );
    """)
    c.close()
    apply_schema(db, Path(__file__).resolve().parents[1]/"database/information_capture_v0013_additive.sql")
    c=sqlite3.connect(db)
    names={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "provider_request_observations" in names
    assert "scheduled_event_window_observations" in names


def test_rolling_window_counts_attempts(tmp_path):
    db=tmp_path/"q.db"
    c=sqlite3.connect(db)
    c.executescript("""
    CREATE TABLE source_observations (observation_id TEXT PRIMARY KEY);
    CREATE TABLE provider_request_observations (
      request_id TEXT PRIMARY KEY, provider TEXT, endpoint TEXT, asset_ticker TEXT,
      requested_at TEXT, finished_at TEXT, status TEXT, source_observation_id TEXT, metadata_json TEXT
    );
    """)
    now=datetime.now(timezone.utc)
    c.execute("INSERT INTO provider_request_observations VALUES (?,?,?,?,?,?,?,?,?)",
              ("a","alpha_vantage","EARNINGS_ESTIMATES","AAPL",(now-timedelta(hours=1)).isoformat(),None,"FAILED",None,"{}"))
    c.execute("INSERT INTO provider_request_observations VALUES (?,?,?,?,?,?,?,?,?)",
              ("b","alpha_vantage","EARNINGS_ESTIMATES","MSFT",(now-timedelta(hours=25)).isoformat(),None,"SUCCESS",None,"{}"))
    c.commit(); c.close()
    q=requests_in_window(db,"alpha_vantage",24,now=now)
    assert q["requests"]==1
    assert q["by_status"]["FAILED"]==1


def test_dell_like_shorter_complete_coverage_is_valid(tmp_path):
    db=tmp_path/"c.db"
    c=sqlite3.connect(db)
    c.executescript("""
    CREATE TABLE expectation_observations (
      source_observation_id TEXT, asset_ticker TEXT, available_at TEXT,
      metric_key TEXT, statistic_key TEXT, fiscal_period TEXT, metadata_json TEXT
    );
    """)
    expected=[("eps","average"),("eps","high"),("eps","low"),
              ("revenue","average"),("revenue","high"),("revenue","low"),
              ("analyst_count","count")]
    for i in range(35):
        for m,s in expected:
            c.execute("INSERT INTO expectation_observations VALUES (?,?,?,?,?,?,?)",
                      ("src","DELL","2026-08-28T00:00:00+00:00",m,s,f"p{i}",
                       json.dumps({"period_scope":"fiscal_quarter"})))
    c.commit(); c.close()
    out=coverage_audit(db)
    assert out["status"]=="PASS"
    assert out["per_symbol"]["DELL"]["period_groups"]==35
    assert out["per_symbol"]["DELL"]["incomplete_period_groups"]==0
