from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from models.market.daily_v005_market_tradables_benchmark import (
    ROOT,
    DEFAULT_CONFIG,
    load_config,
    load_frames,
    load_v004_oos,
    run_horizon,
)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def main():
    p = argparse.ArgumentParser()
    p.add_argument(
        "--stage",
        required=True,
        choices=("plan", "run", "summary"),
    )
    p.add_argument("--horizon", type=int, choices=(1, 3, 5, 10))
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    a = p.parse_args()

    cfg = load_config(a.config)
    math_db = ROOT / cfg["math_db"]
    external_db = ROOT / cfg["external_state_db"]
    v004_dir = ROOT / cfg["v004_report_dir"]
    out = ROOT / cfg["report_dir"]

    if a.stage == "plan":
        result = {
            "status": "PASS",
            "failures": [],
            "benchmark_version": cfg["version"],
            "horizons": {},
        }
        for h in cfg["horizons_sessions"]:
            market, sector, asset, sf, aaf, _ = load_frames(
                math_db, external_db, h, cfg
            )
            old = load_v004_oos(
                v004_dir / f"h{h}_factorized_oos.csv.gz"
            )

            complete = int(market["external_state_complete"].sum())
            missing_test_states = set(
                old["state_id"].astype(str)
            ) - set(asset["state_id"].astype(str))

            horizon_result = {
                "market_rows": int(len(market)),
                "market_rows_external_complete": complete,
                "market_external_coverage_fraction": float(
                    complete / len(market)
                ),
                "sector_rows": int(len(sector)),
                "asset_rows": int(len(asset)),
                "stored_v004_oos_rows": int(len(old)),
                "v004_oos_states_missing_from_asset_frame": int(
                    len(missing_test_states)
                ),
                "base_market_features": len(cfg["base_market_features"]),
                "external_market_features": len(
                    cfg["external_market_features"]
                ),
                "enriched_market_features": (
                    len(cfg["base_market_features"])
                    + len(cfg["external_market_features"])
                ),
                "sector_features": len(sf),
                "asset_features": len(aaf),
            }

            if complete != len(market):
                result["failures"].append(
                    f"h{h}_external_market_state_not_complete"
                )
            if missing_test_states:
                result["failures"].append(
                    f"h{h}_v004_oos_states_missing"
                )
            if len(cfg["base_market_features"]) != 13:
                result["failures"].append(
                    f"h{h}_unexpected_base_market_feature_count"
                )
            if len(cfg["external_market_features"]) != 22:
                result["failures"].append(
                    f"h{h}_unexpected_external_feature_count"
                )
            if len(aaf) != 20:
                result["failures"].append(
                    f"h{h}_unexpected_asset_feature_count"
                )

            result["horizons"][str(h)] = horizon_result

        if result["failures"]:
            result["status"] = "FAIL"
        write(out / "benchmark_plan.json", result)

    elif a.stage == "run":
        if a.horizon is None:
            raise SystemExit("--horizon required")
        result, oos = run_horizon(
            math_db,
            external_db,
            v004_dir,
            a.horizon,
            cfg,
        )
        write(
            out / f"h{a.horizon}_market_tradables_benchmark.json",
            result,
        )
        keep = [
            "state_id",
            "asset_id",
            "ticker",
            "sector",
            "origin_trading_day",
            "target_trading_day",
            "return_pct",
            "fold_id",
            "pred_train_median",
            "pred_hgb_full",
            "pred_v004_additive_hgb",
            "pred_v004_replay",
            "pred_v005_enriched",
        ]
        oos[keep].to_csv(
            out / f"h{a.horizon}_market_tradables_oos.csv.gz",
            index=False,
            compression="gzip",
        )
    else:
        horizons = {}
        for h in cfg["horizons_sessions"]:
            path = out / f"h{h}_market_tradables_benchmark.json"
            if not path.is_file():
                raise FileNotFoundError(path)
            horizons[str(h)] = json.loads(path.read_text(encoding="utf-8"))

        result = {
            "benchmark_version": cfg["version"],
            "interpretation_contract": {
                "primary": "V005 enriched reconstruction vs V004 additive HGB",
                "absolute_checkpoint": "V005 vs fold train median",
                "same_v004_oos_rows": True,
                "only_market_model_changed": True,
                "do_not_select_best_horizon": True,
                "production_ready": False,
            },
            "horizons": horizons,
        }
        write(out / "market_tradables_benchmark_summary.json", result)

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
