from pathlib import Path
import sqlite3
import numpy as np
import pandas as pd
from features.market.daily_v003_core import compute_own_features,compute_context_features,compute_labels,stable_id

def cfg():return {"feature_version":"test_f","label_version":"test_l","minimum_own_history_days":253,"minimum_cross_section_peers_ex_target":50,"minimum_sector_peers_ex_target":3,"horizons_sessions":[1,3,5,10]}

def prices(assets=55,days=270):
    dates=pd.bdate_range('2020-01-01',periods=days);rows=[]
    for a in range(assets):
        for i,d in enumerate(dates):
            c=100+a*.1+i*.03+np.sin(i/7+a)*.2
            rows.append({"asset_id":a+1,"ticker":f"T{a+1:03d}","sector":f"S{a%5}","trading_day":d.date().isoformat(),"state_time":d.date().isoformat()+"T21:00:00+00:00","open":c-.1,"high":c+.5,"low":c-.5,"close":c,"volume":1_000_000+i*100+a})
    return pd.DataFrame(rows)

def action_db(path):
    with sqlite3.connect(path) as c:
        c.executescript("""CREATE TABLE corporate_action_versions(corporate_action_version_id TEXT PRIMARY KEY,is_present INTEGER);
        CREATE TABLE corporate_action_observations(action_observation_id TEXT PRIMARY KEY,corporate_action_version_id TEXT,asset_id INTEGER,effective_trading_day TEXT,action_type TEXT,observation_sequence INTEGER,observed_at TEXT);""")

def test_stable_id():assert stable_id('x',1,'a')==stable_id('x',1,'a') and stable_id('x',1,'a')!=stable_id('x',2,'a')
def test_context_is_leave_one_out():
    own=compute_own_features(prices(),cfg());states=compute_context_features(own,cfg());assert states.asset_id.nunique()==55;assert states.cross_section_peer_count.min()==54
    r=states.iloc[0];same=states[states.trading_day==r.trading_day];expected=same.loc[same.asset_id!=r.asset_id,'asset_return_1d_pct'].mean();assert abs(r.cross_section_mean_return_1d_pct-expected)<1e-10
def test_sector_excludes_target():
    states=compute_context_features(compute_own_features(prices(),cfg()),cfg());r=states.iloc[0];same=states[(states.trading_day==r.trading_day)&(states.sector==r.sector)&(states.asset_id!=r.asset_id)];assert int(r.sector_peer_count)==len(same) and len(same)>=3
def test_labels_future_only(tmp_path):
    p=prices();own=compute_own_features(p,cfg());states=compute_context_features(own,cfg());db=tmp_path/'a.db';action_db(db);lab=compute_labels(own,states,db,cfg());assert set(lab.horizon_sessions)=={1,3,5,10};u=lab[lab.label_status=='usable'];assert (u.target_trading_day.astype(str)>u.origin_trading_day.astype(str)).all();assert (u.mfe_pct+1e-10>=u.return_pct).all();assert (u.mae_pct-1e-10<=u.return_pct).all();assert (u.realized_path_vol_pct>=0).all()
def test_no_events_or_proxies():
    s=Path('features/market/daily_v003_core.py').read_text();assert 'normalized_event' not in s and 'event_state' not in s and 'SPY' not in s and 'VIX' not in s
def test_docs_records_quarantine():
    s=Path('tools/patch_market_v003_core_docs_v001.py').read_text();assert 'FISV, HUBB, MNST' in s and 'quality-quarantined' in s
