from __future__ import annotations
import argparse,json,os,sqlite3,subprocess,sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DEFAULT_DB=ROOT/"data"/"database"/"market_data_v2.db"
DEFAULT_MANIFEST=ROOT/"config"/"event_brain_pilot_v001.json"

def load_manifest(path): return json.loads(path.read_text(encoding="utf-8"))

def preflight(db,manifest):
    with sqlite3.connect(db) as c:
        mig={str(v):str(n) for v,n in c.execute("SELECT version,name FROM schema_migrations")}
        req={"016":"sec_filing_metadata_versioning","017":"event_normalization",
             "018":"daily_price_asof","019":"event_brain_v001"}
        bad={v:(n,mig.get(v)) for v,n in req.items() if mig.get(v)!=n}
        if bad: raise RuntimeError(f"Migraciones inválidas: {bad}")
        tickers=[x["ticker"].upper() for x in manifest["assets"]]
        rows=c.execute("SELECT UPPER(ticker) FROM assets WHERE UPPER(ticker) IN ({})".format(
            ",".join("?" for _ in tickers)),tickers).fetchall()
        found={r[0] for r in rows}
        missing=[t for t in tickers if t not in found]
        if missing: raise RuntimeError(f"Assets faltantes: {missing}")
        coverage={}
        for t in tickers:
            r=c.execute("""SELECT COUNT(*),MIN(o.trading_day),MAX(o.trading_day)
               FROM price_bar_observations o JOIN assets a ON a.asset_id=o.asset_id
               WHERE UPPER(a.ticker)=?""",(t,)).fetchone()
            coverage[t]={"observations":int(r[0]),"min_day":r[1],"max_day":r[2]}
    return {"status":"PASS","assets":len(tickers),"price_coverage":coverage}

def cmd(args):
    print("$"," ".join(args),flush=True)
    subprocess.run(args,cwd=ROOT,check=True)

def prices(manifest):
    for a in manifest["assets"]:
        cmd([sys.executable,"-m","ingestion.prices.yahoo_daily_v1",
             "--ticker",a["ticker"],"--exchange",a["exchange"],
             "--start",manifest["price_start"],"--end",manifest["price_end_exclusive"],
             "--max-days","3660"])

def sec_metadata(manifest):
    if not os.environ.get("SEC_USER_AGENT"): raise RuntimeError("Falta SEC_USER_AGENT")
    args=[sys.executable,"-m","ingestion.events.sec_edgar_v3",
          "--forms",",".join(manifest["sec_forms"]),
          "--max-filings",str(manifest["sec_max_filings_per_ticker"]),
          "--include-older","--initial-availability-mode","historical","--rate-limit","2"]
    for a in manifest["assets"]: args += ["--ticker",a["ticker"]]
    cmd(args)

def audit(db):
    with sqlite3.connect(db) as c:
        p=c.execute("SELECT COUNT(DISTINCT asset_id),COUNT(*) FROM price_bar_observations").fetchone()
        s=c.execute("""SELECT COUNT(DISTINCT filing_raw_document_id),COUNT(*),
                       SUM(availability_is_point_in_time)
                       FROM sec_filing_metadata_observations""").fetchone()
        e=c.execute("""SELECT COUNT(*),COUNT(DISTINCT asset_id),COUNT(DISTINCT event_type)
                       FROM normalized_event_state_snapshots""").fetchone()
    return {"price_assets":int(p[0]),"price_observations":int(p[1]),
            "sec_filings_with_metadata":int(s[0] or 0),"sec_metadata_observations":int(s[1] or 0),
            "sec_pit_observations":int(s[2] or 0),"event_states":int(e[0] or 0),
            "event_assets":int(e[1] or 0),"event_types":int(e[2] or 0)}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--stage",choices=("preflight","prices","sec-metadata","audit"),required=True)
    p.add_argument("--db",type=Path,default=DEFAULT_DB)
    p.add_argument("--manifest",type=Path,default=DEFAULT_MANIFEST)
    a=p.parse_args(); m=load_manifest(a.manifest)
    if a.stage=="preflight": out=preflight(a.db,m)
    elif a.stage=="prices": preflight(a.db,m); prices(m); out=audit(a.db)
    elif a.stage=="sec-metadata": preflight(a.db,m); sec_metadata(m); out=audit(a.db)
    else: out=audit(a.db)
    print(json.dumps(out,indent=2,ensure_ascii=False))

if __name__=="__main__": main()
