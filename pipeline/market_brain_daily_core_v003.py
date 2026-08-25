from __future__ import annotations
import argparse,json,sqlite3
from pathlib import Path
from evaluation.market.daily_v003_core_audit import audit
from features.market.daily_v003_core import DEFAULT_CONFIG,DEFAULT_OUTPUT_DB,DEFAULT_SOURCE_DB,build,load_config
ROOT=Path(__file__).resolve().parents[1]
DEFAULT_REPORT=ROOT/"reports"/"market_brain_daily_v003"/"core_dataset_audit.json"

def quarantine(source_db:Path):
    with sqlite3.connect(source_db) as c:
        c.row_factory=sqlite3.Row
        rows=c.execute("""SELECT a.asset_id,a.ticker,COALESCE(a.sector,'unknown') sector FROM assets a
        LEFT JOIN (SELECT DISTINCT asset_id FROM daily_price_quality_gated_observations_v001) q ON q.asset_id=a.asset_id
        WHERE a.active=1 AND a.asset_type='equity' AND q.asset_id IS NULL ORDER BY a.ticker""").fetchall()
    return {"status":"PASS","quarantined_assets":[dict(r) for r in rows],
      "policy":"Do not relax quality gates to achieve cosmetic 503/503 coverage."}

def main():
    p=argparse.ArgumentParser(); p.add_argument("--stage",required=True,choices=("contract","quarantine","build","audit"))
    p.add_argument("--source-db",type=Path,default=DEFAULT_SOURCE_DB); p.add_argument("--output-db",type=Path,default=DEFAULT_OUTPUT_DB)
    p.add_argument("--config",type=Path,default=DEFAULT_CONFIG); p.add_argument("--report",type=Path,default=DEFAULT_REPORT); a=p.parse_args()
    if a.stage=="contract": result={"status":"PASS","contract":load_config(a.config)}
    elif a.stage=="quarantine": result=quarantine(a.source_db)
    elif a.stage=="build": result=build(a.source_db,a.output_db,a.config)
    else:
        result=audit(a.source_db,a.output_db,a.config); a.report.parent.mkdir(parents=True,exist_ok=True)
        a.report.write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding="utf-8")
    print(json.dumps(result,indent=2,ensure_ascii=False))
if __name__=="__main__": main()
