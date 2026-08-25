from __future__ import annotations

import argparse,json
from pathlib import Path

from models.market.daily_v004_factorized_benchmark import (
    ROOT,DEFAULT_CONFIG,load_config,load_frames,run_horizon,v003_oos
)
import sqlite3

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
        result={
            "status":"PASS",
            "failures":[],
            "benchmark_version":cfg["version"],
            "horizons":{}
        }
        for h in cfg["horizons_sessions"]:
            m,s,x,sf,aaf,daf=load_frames(db,h)

            with sqlite3.connect(db) as conn:
                raw_usable_rows=int(conn.execute(
                    "SELECT COUNT(*) FROM v004_factor_targets "
                    "WHERE horizon_sessions=?",
                    (int(h),)
                ).fetchone()[0])

            old_oos=v003_oos(old/f"h{h}_oos.csv.gz")
            additive_state_ids=set(x["state_id"].astype(str))
            old_state_ids=set(old_oos["state_id"].astype(str))
            missing_v003_oos=old_state_ids-additive_state_ids

            dynamic_ready=int((
                (x["dynamic_factorization_ready"]==1)
                & x[daf].notna().all(axis=1)
            ).sum())

            horizon_result={
                "market_rows":len(m),
                "sector_rows":len(s),
                "raw_usable_asset_rows":raw_usable_rows,
                "additive_asset_rows":len(x),
                "dynamic_ready_rows":dynamic_ready,
                "additive_coverage_fraction":(
                    float(len(x)/raw_usable_rows) if raw_usable_rows else 0.0
                ),
                "v003_oos_rows":len(old_oos),
                "v003_oos_rows_missing_from_additive":len(missing_v003_oos),
                "market_features":len([
                    c for c in m.columns if c.startswith("market_")
                ]),
                "sector_features":len(sf),
                "additive_asset_features":len(aaf),
                "dynamic_asset_features":len(daf),
            }

            if len(missing_v003_oos):
                result["failures"].append(
                    f"h{h}_additive_missing_v003_oos_rows"
                )
            if len(x) < 0.98*raw_usable_rows:
                result["failures"].append(
                    f"h{h}_additive_coverage_below_98pct"
                )
            # With the current Core contract, every usable label has a
            # complete additive state. Treat any loss as a schema regression,
            # not as an acceptable modeling choice.
            if len(x) != raw_usable_rows:
                result["failures"].append(
                    f"h{h}_additive_not_equal_raw_usable_rows"
                )
            if len(aaf) != 20:
                result["failures"].append(
                    f"h{h}_unexpected_additive_feature_count_{len(aaf)}"
                )
            if len(daf) != 25:
                result["failures"].append(
                    f"h{h}_unexpected_dynamic_feature_count_{len(daf)}"
                )
            if dynamic_ready >= len(x):
                result["failures"].append(
                    f"h{h}_dynamic_subset_not_strictly_smaller"
                )

            result["horizons"][str(h)]=horizon_result

        if result["failures"]:
            result["status"]="FAIL"
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
