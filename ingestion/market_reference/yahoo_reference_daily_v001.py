from __future__ import annotations
import argparse, gzip, hashlib, json, sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import pandas as pd

ROOT=Path(__file__).resolve().parents[2]
DEFAULT_DB=ROOT/"data"/"processed"/"market_reference_daily_v001.db"
DEFAULT_RAW=ROOT/"data"/"raw"/"market_reference"/"yahoo"
DEFAULT_CONFIG=ROOT/"config"/"market_brain_daily_v005_external_state.json"


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def load_config(path=DEFAULT_CONFIG):
    x=json.loads(Path(path).read_text())
    if x["active_stage"]!="market_tradables":
        raise ValueError("Only market_tradables is active in V005 foundation")
    return x


def schema(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS reference_batches(
      batch_id TEXT PRIMARY KEY,
      symbol TEXT NOT NULL,
      requested_start TEXT NOT NULL,
      requested_end TEXT NOT NULL,
      retrieved_at TEXT NOT NULL,
      provider TEXT NOT NULL,
      provider_version TEXT NOT NULL,
      request_json TEXT NOT NULL,
      raw_sha256 TEXT NOT NULL,
      raw_path TEXT NOT NULL,
      row_count INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS reference_daily_observations(
      symbol TEXT NOT NULL,
      trading_day TEXT NOT NULL,
      open REAL, high REAL, low REAL, close REAL, adjusted_close REAL, volume REAL,
      dividends REAL, stock_splits REAL, capital_gains REAL,
      batch_id TEXT NOT NULL,
      observed_at TEXT NOT NULL,
      availability_basis TEXT NOT NULL,
      point_in_time_verified INTEGER NOT NULL DEFAULT 0,
      PRIMARY KEY(symbol,trading_day,batch_id),
      FOREIGN KEY(batch_id) REFERENCES reference_batches(batch_id)
    );
    CREATE INDEX IF NOT EXISTS idx_reference_symbol_day
      ON reference_daily_observations(symbol,trading_day);
    """)


def canonical_rows(frame: pd.DataFrame):
    out=[]
    for idx,row in frame.iterrows():
        def val(name):
            x=row[name] if name in row.index else None
            if pd.isna(x): return None
            return float(x)
        out.append({
            "trading_day":pd.Timestamp(idx).date().isoformat(),
            "open":val("Open"),"high":val("High"),"low":val("Low"),
            "close":val("Close"),"adjusted_close":val("Adj Close"),
            "volume":val("Volume"),"dividends":val("Dividends"),
            "stock_splits":val("Stock Splits"),"capital_gains":val("Capital Gains"),
        })
    return out


def validate(rows):
    failures=[]
    seen=set()
    for r in rows:
        d=r["trading_day"]
        if d in seen: failures.append(f"duplicate_day:{d}")
        seen.add(d)
        vals=[r["open"],r["high"],r["low"],r["close"]]
        if any(v is None for v in vals):
            failures.append(f"missing_ohlc:{d}")
            continue
        o,h,l,c=map(float,vals)
        if h<max(o,l,c) or l>min(o,h,c):
            failures.append(f"invalid_ohlc:{d}")
        if r["volume"] is not None and r["volume"]<0:
            failures.append(f"negative_volume:{d}")
    return failures


def acquire(config_path=DEFAULT_CONFIG, db=DEFAULT_DB, raw_root=DEFAULT_RAW):
    cfg=load_config(config_path)
    import yfinance as yf
    symbols=cfg["stages"]["market_tradables"]["symbols"]
    start=cfg["date_window"]["start"]; end=cfg["date_window"]["end_exclusive"]
    db.parent.mkdir(parents=True,exist_ok=True); raw_root.mkdir(parents=True,exist_ok=True)
    result={"status":"PASS","symbols":{},"failures":[]}
    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA foreign_keys=ON"); schema(conn)
        for symbol in symbols:
            frame=yf.Ticker(symbol).history(
                start=start,end=end,interval="1d",auto_adjust=False,
                actions=True,repair=False,keepna=True
            )
            rows=canonical_rows(frame)
            bad=validate(rows)
            if not rows or bad:
                result["failures"].append(symbol)
                result["symbols"][symbol]={"rows":len(rows),"quality_failures":bad[:20]}
                continue
            payload=json.dumps(
                {"provider":"yfinance","provider_version":yf.__version__,
                 "symbol":symbol,"request":{"start":start,"end":end,"interval":"1d",
                 "auto_adjust":False,"actions":True,"repair":False,"keepna":True},
                 "rows":rows},
                sort_keys=True,separators=(",",":"),allow_nan=False
            ).encode()
            sha=hashlib.sha256(payload).hexdigest()
            dest=raw_root/symbol/f"{sha}.provider.json.gz"; dest.parent.mkdir(parents=True,exist_ok=True)
            if not dest.exists():
                with gzip.open(dest,"wb") as f: f.write(payload)
            batch="mref_"+hashlib.sha256((symbol+sha).encode()).hexdigest()
            retrieved=utc_now()
            conn.execute(
                """
                INSERT OR IGNORE INTO reference_batches(
                    batch_id,
                    symbol,
                    requested_start,
                    requested_end,
                    retrieved_at,
                    provider,
                    provider_version,
                    request_json,
                    raw_sha256,
                    raw_path,
                    row_count
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (batch,symbol,start,end,retrieved,"yfinance",yf.__version__,
                 json.dumps(cfg["source"],sort_keys=True),sha,str(dest),len(rows))
            )
            for r in rows:
                conn.execute("""
                INSERT OR IGNORE INTO reference_daily_observations(
                  symbol,trading_day,open,high,low,close,adjusted_close,volume,
                  dividends,stock_splits,capital_gains,batch_id,observed_at,
                  availability_basis,point_in_time_verified
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)
                """,(symbol,r["trading_day"],r["open"],r["high"],r["low"],r["close"],
                     r["adjusted_close"],r["volume"],r["dividends"],r["stock_splits"],
                     r["capital_gains"],batch,retrieved,
                     cfg["historical_availability_basis"]))
            result["symbols"][symbol]={
                "rows":len(rows),"first_day":rows[0]["trading_day"],
                "last_day":rows[-1]["trading_day"],"quality_failures":[]
            }
        conn.commit()
    if result["failures"]: result["status"]="FAIL"
    return result


if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--config",type=Path,default=DEFAULT_CONFIG)
    p.add_argument("--db",type=Path,default=DEFAULT_DB)
    p.add_argument("--raw-root",type=Path,default=DEFAULT_RAW)
    a=p.parse_args()
    print(json.dumps(acquire(a.config,a.db,a.raw_root),indent=2))
