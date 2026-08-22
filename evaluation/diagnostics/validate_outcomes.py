from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = ROOT / "data" / "database" / "market_data_v2.db"


def validate(db_path: Path) -> dict:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT outcome_id, asset_id, origin_time, horizon_seconds,
                   start_price, end_price, return_pct, mfe_pct, mae_pct,
                   observed_bars, expected_bars, coverage_pct,
                   max_gap_seconds, session_count, path_json
            FROM realized_outcomes
            """
        ).fetchall()

        failures: list[dict] = []
        coverage_values = []
        for row in rows:
            r = dict(row)
            if r["start_price"] is not None and r["start_price"] <= 0:
                failures.append({"id": r["outcome_id"], "reason": "non_positive_start_price"})
            if r["end_price"] is not None and r["end_price"] <= 0:
                failures.append({"id": r["outcome_id"], "reason": "non_positive_end_price"})

            if None not in (r["return_pct"], r["mfe_pct"]) and r["return_pct"] > r["mfe_pct"] + 1e-9:
                failures.append({"id": r["outcome_id"], "reason": "return_above_mfe"})
            if None not in (r["return_pct"], r["mae_pct"]) and r["return_pct"] < r["mae_pct"] - 1e-9:
                failures.append({"id": r["outcome_id"], "reason": "return_below_mae"})

            if r["coverage_pct"] is not None:
                coverage_values.append(r["coverage_pct"])
                if not (0 <= r["coverage_pct"] <= 100):
                    failures.append({"id": r["outcome_id"], "reason": "coverage_out_of_range"})

            if r["observed_bars"] is not None and r["expected_bars"] is not None:
                if r["observed_bars"] > r["expected_bars"]:
                    failures.append({"id": r["outcome_id"], "reason": "observed_gt_expected"})

        horizon_stats = [
            dict(row)
            for row in conn.execute(
                """
                SELECT horizon_seconds,
                       COUNT(*) AS n,
                       AVG(return_pct) AS avg_return_pct,
                       MIN(return_pct) AS min_return_pct,
                       MAX(return_pct) AS max_return_pct,
                       AVG(mfe_pct) AS avg_mfe_pct,
                       AVG(mae_pct) AS avg_mae_pct,
                       AVG(coverage_pct) AS avg_coverage_pct
                FROM realized_outcomes
                GROUP BY horizon_seconds
                ORDER BY horizon_seconds
                """
            )
        ]

        return {
            "db": str(db_path),
            "outcomes": len(rows),
            "horizon_stats": horizon_stats,
            "avg_coverage_pct": (sum(coverage_values) / len(coverage_values)) if coverage_values else None,
            "failures_count": len(failures),
            "failures_sample": failures[:25],
            "status": "PASS" if not failures else "FAIL",
        }
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    print(json.dumps(validate(args.db), indent=2))
