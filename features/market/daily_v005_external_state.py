from __future__ import annotations
import json, sqlite3
from pathlib import Path
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[2]
DEFAULT_REFERENCE_DB=ROOT/"data"/"processed"/"market_reference_daily_v001.db"
DEFAULT_OUTPUT=ROOT/"data"/"processed"/"market_daily_v005_external_state.db"
DEFAULT_CONFIG=ROOT/"config"/"market_brain_daily_v005_external_state.json"


def _returns(s,w):
    return 100*((s/s.shift(w))-1)

def _vol(r,w):
    return r.rolling(w,min_periods=w).std(ddof=0)

def _drawdown(close,w):
    return 100*(close/close.rolling(w,min_periods=w).max()-1)


def latest_reference(conn,symbol):
    return pd.read_sql_query("""
      WITH ranked AS (
        SELECT *, ROW_NUMBER() OVER(
          PARTITION BY symbol,trading_day ORDER BY observed_at DESC,batch_id DESC
        ) AS rn
        FROM reference_daily_observations WHERE symbol=?
      )
      SELECT * FROM ranked WHERE rn=1 ORDER BY trading_day
    """,conn,params=(symbol,))


def build(reference_db=DEFAULT_REFERENCE_DB, output_db=DEFAULT_OUTPUT, config=DEFAULT_CONFIG):
    cfg=json.loads(Path(config).read_text())
    with sqlite3.connect(reference_db) as conn:
        frames={s:latest_reference(conn,s) for s in ["SPY","QQQ","IWM"]}
    for s,df in frames.items():
        if df.empty: raise RuntimeError(f"missing reference {s}")
        df["trading_day"]=df["trading_day"].astype(str)
        for c in ["open","high","low","close","volume"]:
            df[c]=pd.to_numeric(df[c],errors="coerce")
    base=pd.DataFrame({"trading_day":sorted(set(frames["SPY"]["trading_day"]))})
    for s,df in frames.items():
        z=df[["trading_day","open","high","low","close","volume"]].copy()
        z=z.rename(columns={c:f"{s.lower()}_{c}" for c in z.columns if c!="trading_day"})
        base=base.merge(z,on="trading_day",how="left",validate="one_to_one")

    for sym in ["spy","qqq","iwm"]:
        close=base[f"{sym}_close"]
        for w in [1,5,20]:
            base[f"{sym}_return_{w}d_pct"]=_returns(close,w)
    base["spy_return_63d_pct"]=_returns(base["spy_close"],63)
    spy_r1=base["spy_return_1d_pct"]
    base["spy_vol_20d_pct"]=_vol(spy_r1,20)
    base["spy_vol_63d_pct"]=_vol(spy_r1,63)
    base["spy_drawdown_63d_pct"]=_drawdown(base["spy_close"],63)
    base["spy_drawdown_252d_pct"]=_drawdown(base["spy_close"],252)
    base["spy_range_1d_pct"]=100*(base["spy_high"]-base["spy_low"])/base["spy_close"]
    base["spy_volume_ratio_20d"]=base["spy_volume"]/base["spy_volume"].rolling(20,min_periods=20).mean()
    for w in [1,5,20]:
        base[f"qqq_minus_spy_{w}d_pct"]=base[f"qqq_return_{w}d_pct"]-base[f"spy_return_{w}d_pct"]
        base[f"iwm_minus_spy_{w}d_pct"]=base[f"iwm_return_{w}d_pct"]-base[f"spy_return_{w}d_pct"]

    features=cfg["features"]
    state=base[["trading_day",*features]].copy()
    state["feature_version"]=cfg["feature_version"]
    state["point_in_time_verified"]=0
    state["availability_basis"]=cfg["historical_availability_basis"]

    output_db.parent.mkdir(parents=True,exist_ok=True)
    if output_db.exists(): output_db.unlink()
    with sqlite3.connect(output_db) as conn:
        state.to_sql("market_external_state_v005",conn,index=False)
        conn.execute("CREATE UNIQUE INDEX idx_v005_day ON market_external_state_v005(trading_day)")
    return {
        "status":"PASS","rows":len(state),
        "first_day":str(state.trading_day.min()),"last_day":str(state.trading_day.max()),
        "complete_feature_rows":int(np.isfinite(state[features].to_numpy(float)).all(axis=1).sum()),
        "features":len(features)
    }
