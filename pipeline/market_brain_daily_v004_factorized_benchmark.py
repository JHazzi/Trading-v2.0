from __future__ import annotations

import argparse,json
from pathlib import Path

from models.market.daily_v004_factorized_benchmark import (
    ROOT,DEFAULT_CONFIG,load_config,load_frames,run_horizon
)

def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,indent=2),encoding="utf-8")

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--stage",required=True,choices=("plan","run","summary"))
    p.add_argument("--horizon",type=int,choices=(1,3,5,10))
    p.add_argument("--config",type=Path,default=DEFAULT_CONFIG)
    a=p.parse_args()
    cfg=load_config(a.config)
    db=ROOT/cfg["math_db"]; old=ROOT/cfg["v003_report_dir"]; out=ROOT/cfg["report_dir"]

    if a.stage=="plan":
        result={"status":"PASS","benchmark_version":cfg["version"],"horizons":{}}
        for h in cfg["horizons_sessions"]:
            m,s,x,sf,af=load_frames(db,h)
            result["horizons"][str(h)]={
                "market_rows":len(m),"sector_rows":len(s),"asset_rows":len(x),
                "dynamic_ready_rows":int(x.dynamic_factorization_ready.sum()),
                "market_features":len([c for c in m.columns if c.startswith("market_")]),
                "sector_features":len(sf),"asset_features":len(af),
            }
        write(out/"benchmark_plan.json",result)
    elif a.stage=="run":
        if a.horizon is None: raise SystemExit("--horizon required")
        result,oos=run_horizon(db,old,a.horizon,cfg)
        write(out/f"h{a.horizon}_factorized_benchmark.json",result)
        keep=[
            "state_id","asset_id","ticker","sector","origin_trading_day",
            "target_trading_day","return_pct","fold_id",
            "pred_train_median","pred_hgb_full",
            "pred_additive_hgb","pred_additive_ridge",
            "pred_dynamic_hgb","pred_dynamic_ridge"
        ]
        oos[keep].to_csv(out/f"h{a.horizon}_factorized_oos.csv.gz",
                         index=False,compression="gzip")
    else:
        horizons={}
        for h in cfg["horizons_sessions"]:
            path=out/f"h{h}_factorized_benchmark.json"
            if not path.is_file(): raise FileNotFoundError(path)
            horizons[str(h)]=json.loads(path.read_text())
        result={
            "benchmark_version":cfg["version"],
            "interpretation_contract":{
                "primary":"HGB additive reconstruction vs V003 fold train median",
                "V003_full_is_secondary_reference":True,
                "dynamic_beta_is_secondary":True,
                "do_not_select_best_horizon":True,
                "production_ready":False,
            },
            "horizons":horizons
        }
        write(out/"factorized_benchmark_summary.json",result)
    print(json.dumps(result,indent=2))

if __name__=="__main__":
    main()
