from __future__ import annotations
import json, sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

from research.information_sources.expectation_quality_v0011 import quality_audit, revision_diff
from ingestion.expectations.alphavantage_scaling_v0011 import read_symbols, plan_due_symbols

SCHEMA = """
CREATE TABLE source_observations(observation_id TEXT PRIMARY KEY, source_ref TEXT, available_at TEXT, retrieved_at TEXT, strict_pit INTEGER, raw_payload_json TEXT);
CREATE TABLE expectation_observations(observation_id TEXT PRIMARY KEY, asset_ticker TEXT, entity_key TEXT, expectation_type TEXT, metric_key TEXT, fiscal_period TEXT, statistic_key TEXT, value_real REAL, value_text TEXT, available_at TEXT, source_observation_id TEXT, metadata_json TEXT);
"""

def db(tmp_path: Path) -> Path:
    p=tmp_path/'x.db'; c=sqlite3.connect(p); c.executescript(SCHEMA); c.commit(); c.close(); return p

def add_snapshot(p: Path, sym: str, sid: str, t: str, val: float, duplicate=False):
    c=sqlite3.connect(p)
    c.execute("INSERT INTO source_observations VALUES(?,?,?,?,?,?)", (sid,'alpha_vantage:EARNINGS_ESTIMATES:'+sym,t,t,1,'{}'))
    rows=[('x'+sid,sym,sym,'analyst_consensus','eps','2026Q4','average',val,None,t,sid,json.dumps({'provider_horizon':'next fiscal quarter','provider_source_field':'eps_estimate_average'}))]
    if duplicate: rows.append(('y'+sid,sym,sym,'analyst_consensus','eps','2026Q4','average',val+1,None,t,sid,'{}'))
    c.executemany("INSERT INTO expectation_observations VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", rows); c.commit(); c.close()

def cfg():
    return {'feature_visibility':'blocked','request_policy':{'default_daily_request_budget':2},'cadence_policy':{'deep_cohort':['AAPL'],'deep_cohort_days':1,'earnings_within_7d_days':1,'earnings_within_30d_days':2,'broad_universe_days':7}}

def test_read_symbols_text(tmp_path):
    p=tmp_path/'s.txt'; p.write_text('aapl\nMSFT\naapl\n'); assert read_symbols(symbols_file=str(p))==['AAPL','MSFT']

def test_quality_collision(tmp_path):
    p=db(tmp_path); add_snapshot(p,'AAPL','s1','2026-08-28T00:00:00+00:00',1.0,True); q=quality_audit(p); assert q['same_snapshot_series_collision_count']==1

def test_revision_diff(tmp_path):
    p=db(tmp_path); add_snapshot(p,'AAPL','s1','2026-08-27T00:00:00+00:00',1.0); add_snapshot(p,'AAPL','s2','2026-08-28T00:00:00+00:00',2.0); r=revision_diff(p,'AAPL'); assert r['changed_series']==1

def test_revision_insufficient(tmp_path):
    p=db(tmp_path); add_snapshot(p,'AAPL','s1','2026-08-28T00:00:00+00:00',1.0); assert revision_diff(p,'AAPL')['status']=='INSUFFICIENT_SNAPSHOTS'

def test_plan_budget_and_deep_priority(tmp_path):
    p=db(tmp_path); now=datetime(2026,8,28,tzinfo=timezone.utc); plan=plan_due_symbols(p,['MSFT','AAPL','JPM'],cfg(),now); assert plan['selected_count']==2 and plan['selected'][0]['symbol']=='AAPL'
