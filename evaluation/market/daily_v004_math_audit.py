from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = ROOT / "data" / "processed" / "market_daily_v004_math.db"
DEFAULT_REPORT = ROOT / "reports" / "market_brain_daily_v004" / "math_foundation_audit.json"


def _finite_absmax(series: pd.Series) -> float:
    x = pd.to_numeric(series, errors="coerce").to_numpy(float)
    x = x[np.isfinite(x)]
    return float(np.max(np.abs(x))) if len(x) else float("nan")


def audit(db: Path = DEFAULT_DB) -> dict[str, Any]:
    if not db.is_file():
        return {"status": "FAIL", "failures": ["v004_math_db_missing"]}

    with sqlite3.connect(db) as conn:
        market = pd.read_sql_query("SELECT * FROM v004_market_states", conn)
        sector = pd.read_sql_query("SELECT * FROM v004_sector_states", conn)
        asset = pd.read_sql_query("SELECT * FROM v004_asset_states", conn)
        targets = pd.read_sql_query("SELECT * FROM v004_factor_targets", conn)

    failures, reviews = [], []

    if market["trading_day"].duplicated().any():
        failures.append("duplicate_market_day")
    if sector.duplicated(["trading_day", "sector"]).any():
        failures.append("duplicate_sector_day")
    if asset["state_id"].duplicated().any():
        failures.append("duplicate_asset_state")

    add_err = _finite_absmax(targets["additive_identity_error"])
    beta_ready = targets["dynamic_factorization_ready"].astype(bool)
    beta_err = _finite_absmax(
        targets.loc[beta_ready, "beta_identity_error"]
    )
    if not np.isfinite(add_err) or add_err > 1e-8:
        failures.append("additive_identity_broken")
    if beta_ready.any() and (
        not np.isfinite(beta_err) or beta_err > 1e-8
    ):
        failures.append("dynamic_beta_identity_broken")

    beta_cov = {}
    for h, g in targets.groupby("horizon_sessions"):
        ready = float(g["dynamic_factorization_ready"].mean())
        beta_cov[str(int(h))] = {
            "rows": int(len(g)),
            "dynamic_factorization_ready_rows": int(
                g["dynamic_factorization_ready"].sum()
            ),
            "dynamic_factorization_ready_fraction": ready,
        }
        if ready < 0.60:
            reviews.append(f"h{int(h)}_dynamic_factor_coverage_below_60pct")

    beta_stats = {}
    for col in (
        "beta_market_63", "beta_market_252",
        "gamma_sector_63", "gamma_sector_252",
        "idio_vol_63d_pct",
    ):
        x = pd.to_numeric(asset[col], errors="coerce")
        finite = x[np.isfinite(x)]
        beta_stats[col] = {
            "finite_rows": int(len(finite)),
            "finite_fraction": float(len(finite) / len(asset)),
            "median": None if finite.empty else float(finite.median()),
            "p01": None if finite.empty else float(finite.quantile(.01)),
            "p99": None if finite.empty else float(finite.quantile(.99)),
        }

    # Oracle decomposition remains diagnostic only.  Report the amount of
    # remaining absolute error after subtracting realized future factors.
    decomposition = {}
    for h, g in targets.groupby("horizon_sessions"):
        y = g["return_pct"].to_numpy(float)
        market_resid = (
            g["return_pct"] - g["future_market_return_pct"]
        ).to_numpy(float)
        sector_resid = g["target_asset_additive_residual_pct"].to_numpy(float)
        decomposition[str(int(h))] = {
            "rows": int(len(g)),
            "zero_mae_pct": float(np.mean(np.abs(y))),
            "oracle_market_removed_mae_pct": float(np.mean(np.abs(market_resid))),
            "oracle_market_and_sector_removed_mae_pct": float(
                np.mean(np.abs(sector_resid))
            ),
            "future_market_factor_std_pct": float(
                g.groupby("origin_trading_day")[
                    "future_market_return_pct"
                ].first().std(ddof=0)
            ),
        }

    status = "FAIL" if failures else ("REVIEW" if reviews else "PASS")
    return {
        "status": status,
        "failures": sorted(set(failures)),
        "reviews": sorted(set(reviews)),
        "counts": {
            "market_states": int(len(market)),
            "sector_states": int(len(sector)),
            "asset_states": int(len(asset)),
            "assets": int(asset["asset_id"].nunique()),
            "sectors": int(asset["sector"].nunique()),
            "first_day": str(asset["trading_day"].min()),
            "last_day": str(asset["trading_day"].max()),
        },
        "identities": {
            "additive_max_abs_error": add_err,
            "dynamic_beta_max_abs_error_ready_rows": beta_err,
        },
        "dynamic_factor_coverage": beta_cov,
        "dynamic_factor_state": beta_stats,
        "target_decomposition": decomposition,
        "next_gate": (
            "If identities and coverage are healthy, preregister three "
            "separate walk-forward benchmarks: market factor, sector residual, "
            "and asset residual, plus reconstructed absolute return."
        ),
        "claim_boundary": (
            "This audit proves dataset/math consistency only. It does not "
            "demonstrate predictability or trading alpha."
        ),
    }


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    p.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    a = p.parse_args()
    result = audit(a.db)
    a.report.parent.mkdir(parents=True, exist_ok=True)
    a.report.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
