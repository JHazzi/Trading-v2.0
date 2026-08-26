from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from features.market.daily_v0052_financial_conditions import equity_session_set

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "config" / "market_brain_daily_v0052_financial_conditions.json"
DEFAULT_REPORT = ROOT / "reports" / "market_brain_daily_v0052" / "financial_conditions_foundation_audit.json"


def audit(config_path: Path = DEFAULT_CONFIG, report_path: Path = DEFAULT_REPORT) -> dict:
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    main_db = ROOT / cfg["main_db"]
    vix_db = ROOT / cfg["vix_db"]
    state_db = ROOT / cfg["state_db"]
    math_db = ROOT / cfg["v004_math_db"]
    failures, reviews = [], []

    with sqlite3.connect(main_db) as conn:
        assets = pd.read_sql_query(
            """
            SELECT asset_id,ticker,asset_type,exchange,active,source
            FROM assets
            WHERE UPPER(ticker) IN ('SHY','IEF','TLT','HYG','LQD')
            ORDER BY ticker
            """, conn,
        )
        price_cov = pd.read_sql_query(
            """
            SELECT a.ticker,COUNT(*) AS rows,MIN(o.trading_day) AS first_day,
                   MAX(o.trading_day) AS last_day,MAX(o.point_in_time_verified) AS max_pit
            FROM assets a
            JOIN price_bar_observations o ON o.asset_id=a.asset_id
            WHERE UPPER(a.ticker) IN ('SHY','IEF','TLT','HYG','LQD')
              AND o.source_id='yahoo_finance' AND o.interval='1d'
              AND o.observation_sequence=1
            GROUP BY a.ticker ORDER BY a.ticker
            """, conn,
        )
        quality_fail = pd.read_sql_query(
            """
            SELECT a.ticker,COUNT(*) AS failed_checks
            FROM assets a JOIN price_quality_results q ON q.asset_id=a.asset_id
            WHERE UPPER(a.ticker) IN ('SHY','IEF','TLT','HYG','LQD')
              AND q.check_status='fail'
            GROUP BY a.ticker
            """, conn,
        )
        action_cov = pd.read_sql_query(
            """
            SELECT a.ticker,COUNT(*) AS cash_action_rows
            FROM assets a
            JOIN corporate_action_observations o ON o.asset_id=a.asset_id
            WHERE UPPER(a.ticker) IN ('SHY','IEF','TLT','HYG','LQD')
              AND o.source_id='yahoo_finance'
              AND o.observation_sequence=1
              AND o.action_type IN ('dividend','capital_gain')
            GROUP BY a.ticker ORDER BY a.ticker
            """, conn,
        )

    expected = set(cfg["reference_assets"])
    if set(assets["ticker"].str.upper()) != expected:
        failures.append("reference_asset_set_mismatch")
    actual_exchange = {r.ticker.upper(): str(r.exchange).upper() for r in assets.itertuples()}
    for ticker, meta in cfg["reference_assets"].items():
        if actual_exchange.get(ticker) != meta["exchange"]:
            failures.append(f"{ticker}_exchange_mismatch")
    for row in assets.itertuples():
        if int(row.active) != 0:
            reviews.append(f"{row.ticker}_reference_asset_active")
    if set(price_cov["ticker"].str.upper()) != expected:
        failures.append("price_reference_coverage_missing_symbol")
    if not quality_fail.empty:
        failures.append("reference_price_quality_failure")

    with sqlite3.connect(vix_db) as conn:
        vix = pd.read_sql_query(
            """
            SELECT o.trading_day,o.available_at,o.availability_basis,
                   o.point_in_time_verified,o.observation_kind
            FROM global_reference_observations o
            WHERE o.source_id='cboe_vix_daily' AND o.symbol='VIX'
              AND o.observation_sequence=1
            ORDER BY o.trading_day
            """, conn,
        )

    if vix.empty:
        failures.append("missing_vix_observations")
        eligible_vix_days = set()
        non_equity_vix = vix.copy()
    else:
        eligible_vix_days = equity_session_set(
            str(vix["trading_day"].min()),
            str(vix["trading_day"].max()),
        )
        non_equity_vix = vix[
            ~vix["trading_day"].astype(str).isin(eligible_vix_days)
        ].copy()
        if not non_equity_vix.empty:
            bad_basis = non_equity_vix[
                non_equity_vix["availability_basis"]
                != "provider_non_equity_session_retrieval_only_not_model_eligible"
            ]
            if not bad_basis.empty:
                failures.append("non_equity_vix_row_has_model_clock_basis")
            if not (
                non_equity_vix["observation_kind"]
                == "initial_backfill_non_equity_session"
            ).all():
                failures.append("non_equity_vix_row_kind_mismatch")
    with sqlite3.connect(state_db) as conn:
        state = pd.read_sql_query("SELECT * FROM market_financial_conditions_v0052 ORDER BY trading_day", conn)
    with sqlite3.connect(math_db) as conn:
        market = pd.read_sql_query("SELECT trading_day FROM v004_market_states ORDER BY trading_day", conn)

    state_days = set(state["trading_day"].astype(str))
    leaked_non_equity_vix_days = (
        set(non_equity_vix["trading_day"].astype(str)) & state_days
    )
    if leaked_non_equity_vix_days:
        failures.append("non_equity_vix_provider_date_leaked_into_state")

    pure = sum(cfg["pure_external_features"].values(), [])
    finite = np.isfinite(state[pure].to_numpy(float)).all(axis=1)
    complete_days = set(state.loc[finite, "trading_day"].astype(str))
    market_days = set(market["trading_day"].astype(str))
    overlap = complete_days & market_days
    ratio = len(overlap) / len(market_days) if market_days else 0.0

    if ratio < 0.98:
        failures.append("v004_market_overlap_below_98pct")
    if int(state["vix_feature_lag_sessions"].max()) != 1:
        failures.append("same_day_vix_leakage_guard_failed")
    if int(state["adjusted_close_used"].max()) != 0:
        failures.append("adjusted_close_used_in_reference_features")
    if int(state["historical_strict_pit"].max()) != 0:
        failures.append("historical_reference_incorrectly_marked_pit")
    if not vix.empty and int(vix["point_in_time_verified"].max() or 0) != 0:
        failures.append("vix_incorrectly_marked_pit")
    if set(action_cov["ticker"].str.upper()) != expected:
        reviews.append("one_or_more_reference_etfs_have_no_cash_actions")

    status = "FAIL" if failures else ("REVIEW" if reviews else "PASS")
    result = {
        "status": status,
        "failures": sorted(set(failures)),
        "reviews": sorted(set(reviews)),
        "reference_assets": assets.to_dict("records"),
        "price_reference_coverage": price_cov.to_dict("records"),
        "cash_action_coverage": action_cov.to_dict("records"),
        "vix_provider_rows": int(len(vix)),
        "vix_model_eligible_equity_session_rows": int(
            vix["trading_day"].astype(str).isin(eligible_vix_days).sum()
            if not vix.empty else 0
        ),
        "vix_non_equity_provider_rows": int(len(non_equity_vix)),
        "vix_non_equity_provider_days": (
            non_equity_vix["trading_day"].astype(str).tolist()[:50]
        ),
        "state_rows": int(len(state)),
        "pure_external_features": len(pure),
        "complete_feature_rows": int(finite.sum()),
        "v004_market_days": int(len(market_days)),
        "complete_overlap_days": int(len(overlap)),
        "overlap_fraction": float(ratio),
        "causal_guards": {
            "same_day_vix_used": False,
            "vix_lag_sessions": 1,
            "vix_lag_sequence_basis": "previous_XNYS_session",
            "provider_non_equity_vix_rows_preserved": True,
            "provider_non_equity_vix_rows_model_eligible": False,
            "provider_non_equity_vix_rows_leaked_into_state": bool(
                leaked_non_equity_vix_days
            ),
            "adjusted_close_used": False,
            "price_observation_policy": cfg["etf_ingestion"]["feature_observation_policy"],
            "action_observation_policy": cfg["etf_ingestion"]["action_observation_policy"],
            "return_convention": cfg["etf_ingestion"]["return_convention"],
            "cash_action_availability_basis": cfg["etf_ingestion"]["cash_action_availability_basis"],
            "strict_historical_pit": False,
        },
        "next_gate": "If PASS/acceptable REVIEW, preregister/run V005.2 benchmark plan. Do not activate sector ETFs, macro, events or regime models.",
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    p.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    a = p.parse_args()
    print(json.dumps(audit(a.config, a.report), indent=2))
