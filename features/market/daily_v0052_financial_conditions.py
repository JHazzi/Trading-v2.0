from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "config" / "market_brain_daily_v0052_financial_conditions.json"


def rolling_zscore(x: pd.Series, sessions: int) -> pd.Series:
    mean = x.rolling(sessions, min_periods=sessions).mean()
    std = x.rolling(sessions, min_periods=sessions).std(ddof=0)
    return (x - mean) / std.where(std > 1e-12)


def compound_return(daily_return_pct: pd.Series, sessions: int) -> pd.Series:
    gross = 1.0 + daily_return_pct / 100.0
    return 100.0 * (gross.rolling(sessions, min_periods=sessions).apply(np.prod, raw=True) - 1.0)


def total_return_from_close_and_cash(close: pd.Series, cash: pd.Series) -> pd.Series:
    previous = close.shift(1)
    return 100.0 * ((close + cash) / previous - 1.0)


def initial_etf_prices(conn: sqlite3.Connection, ticker: str) -> pd.DataFrame:
    return pd.read_sql_query(
        """
        SELECT
          o.trading_day,
          v.open,v.high,v.low,v.close,v.volume,
          o.available_at,o.availability_basis,o.point_in_time_verified
        FROM assets AS a
        JOIN price_bar_observations AS o
          ON o.asset_id=a.asset_id
        JOIN price_bar_versions AS v
          ON v.price_bar_version_id=o.price_bar_version_id
        WHERE UPPER(a.ticker)=UPPER(?)
          AND o.source_id='yahoo_finance'
          AND o.interval='1d'
          AND o.observation_sequence=1
        ORDER BY o.trading_day
        """,
        conn,
        params=(ticker,),
    )


def initial_cash_actions(conn: sqlite3.Connection, ticker: str) -> pd.DataFrame:
    return pd.read_sql_query(
        """
        SELECT
          o.effective_trading_day AS trading_day,
          SUM(CASE WHEN v.is_present=1 THEN COALESCE(v.raw_value,0.0) ELSE 0.0 END)
            AS cash_distribution
        FROM assets AS a
        JOIN corporate_action_observations AS o
          ON o.asset_id=a.asset_id
        JOIN corporate_action_versions AS v
          ON v.corporate_action_version_id=o.corporate_action_version_id
        WHERE UPPER(a.ticker)=UPPER(?)
          AND o.source_id='yahoo_finance'
          AND o.observation_sequence=1
          AND o.action_type IN ('dividend','capital_gain')
        GROUP BY o.effective_trading_day
        ORDER BY o.effective_trading_day
        """,
        conn,
        params=(ticker,),
    )


def initial_vix(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query(
        """
        SELECT o.trading_day,v.open,v.high,v.low,v.close,
               o.available_at,o.availability_basis,o.point_in_time_verified
        FROM global_reference_observations AS o
        JOIN global_reference_versions AS v
          ON v.version_id=o.version_id
        WHERE o.source_id='cboe_vix_daily'
          AND o.symbol='VIX'
          AND o.observation_sequence=1
        ORDER BY o.trading_day
        """,
        conn,
    )



def equity_session_set(start_day: str, end_day: str) -> set[str]:
    import exchange_calendars

    start = date.fromisoformat(start_day)
    end = date.fromisoformat(end_day)
    cal = exchange_calendars.get_calendar(
        "XNYS",
        start=start - timedelta(days=7),
        end=end + timedelta(days=7),
    )
    return {
        session.date().isoformat()
        for session in cal.sessions
        if start_day <= session.date().isoformat() <= end_day
    }


def filter_vix_to_equity_sessions(vix: pd.DataFrame) -> pd.DataFrame:
    if vix.empty:
        return vix.copy()
    eligible = equity_session_set(
        str(vix["trading_day"].min()),
        str(vix["trading_day"].max()),
    )
    return (
        vix[vix["trading_day"].astype(str).isin(eligible)]
        .copy()
        .sort_values("trading_day")
        .reset_index(drop=True)
    )


def build(config_path: Path = DEFAULT_CONFIG) -> dict:
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    main_db = ROOT / cfg["main_db"]
    vix_db = ROOT / cfg["vix_db"]
    output_db = ROOT / cfg["state_db"]

    with sqlite3.connect(main_db) as conn:
        etfs = {}
        for ticker in cfg["reference_assets"]:
            key = ticker.lower()
            frame = initial_etf_prices(conn, ticker)
            actions = initial_cash_actions(conn, ticker)
            if frame.empty:
                raise RuntimeError(f"missing ETF reference data: {ticker}")
            frame["trading_day"] = frame["trading_day"].astype(str)
            frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
            if actions.empty:
                frame["cash_distribution"] = 0.0
            else:
                actions["trading_day"] = actions["trading_day"].astype(str)
                frame = frame.merge(actions, on="trading_day", how="left", validate="one_to_one")
                frame["cash_distribution"] = pd.to_numeric(
                    frame["cash_distribution"], errors="coerce"
                ).fillna(0.0)
            etfs[key] = frame

    with sqlite3.connect(vix_db) as conn:
        vix_provider = initial_vix(conn)
    if vix_provider.empty:
        raise RuntimeError("missing Cboe VIX data")
    vix_provider["trading_day"] = vix_provider["trading_day"].astype(str)
    vix_provider["close"] = pd.to_numeric(
        vix_provider["close"], errors="coerce"
    )

    # Provider-only dates outside XNYS are preserved in the reference DB but
    # excluded before rolling/lag operations. Thus lag1 means previous equity
    # session, not previous arbitrary provider date.
    vix = filter_vix_to_equity_sessions(vix_provider)
    excluded_vix_provider_rows = int(len(vix_provider) - len(vix))
    if vix.empty:
        raise RuntimeError("no model-eligible VIX equity-session rows")

    # Use the U.S. Treasury ETF session spine; all reference ETFs should share it.
    base = pd.DataFrame({"trading_day": etfs["shy"]["trading_day"]}).drop_duplicates()
    base = base.sort_values("trading_day").reset_index(drop=True)

    for ticker, frame in etfs.items():
        z = frame[["trading_day", "close", "cash_distribution"]].copy().rename(
            columns={
                "close": f"{ticker}_close",
                "cash_distribution": f"{ticker}_cash_distribution",
            }
        )
        base = base.merge(z, on="trading_day", how="left", validate="one_to_one")

    # Causal-by-formula total return proxy: today's effective cash distribution
    # may enter today's close-to-close return; future distributions never do.
    for ticker in ("shy", "ief", "tlt", "hyg", "lqd"):
        r1 = total_return_from_close_and_cash(
            base[f"{ticker}_close"],
            base[f"{ticker}_cash_distribution"],
        )
        base[f"{ticker}_return_1d_pct"] = r1
        base[f"{ticker}_return_5d_pct"] = compound_return(r1, 5)
        base[f"{ticker}_return_20d_pct"] = compound_return(r1, 20)
        base[f"{ticker}_vol_20d_pct"] = r1.rolling(20, min_periods=20).std(ddof=0)

    base["ief_minus_shy_5d_pct"] = base["ief_return_5d_pct"] - base["shy_return_5d_pct"]
    base["tlt_minus_ief_5d_pct"] = base["tlt_return_5d_pct"] - base["ief_return_5d_pct"]
    base["tlt_minus_shy_20d_pct"] = base["tlt_return_20d_pct"] - base["shy_return_20d_pct"]
    for w in (1, 5, 20):
        base[f"hyg_minus_lqd_{w}d_pct"] = base[f"hyg_return_{w}d_pct"] - base[f"lqd_return_{w}d_pct"]

    # VIX daily close is later than the equity-origin clock. Filter to XNYS
    # sessions first, then shift one full eligible equity session.
    vz = vix[["trading_day", "close"]].copy()
    vz["vix_lag1_close"] = vz["close"].shift(1)
    vz["vix_lag1_delta_1d_points"] = vz["vix_lag1_close"] - vz["vix_lag1_close"].shift(1)
    vz["vix_lag1_delta_5d_points"] = vz["vix_lag1_close"] - vz["vix_lag1_close"].shift(5)
    vz["vix_lag1_zscore_63d"] = rolling_zscore(vz["vix_lag1_close"], 63)
    vz["vix_lag1_zscore_252d"] = rolling_zscore(vz["vix_lag1_close"], 252)
    log_change = 100.0 * np.log(vz["vix_lag1_close"] / vz["vix_lag1_close"].shift(1))
    vz["vix_lag1_log_change_vol_20d"] = log_change.rolling(20, min_periods=20).std(ddof=0)

    vix_features = cfg["pure_external_features"]["vix"]
    base = base.merge(
        vz[["trading_day", *vix_features]],
        on="trading_day",
        how="left",
        validate="one_to_one",
    )

    pure = sum(cfg["pure_external_features"].values(), [])
    state = base[["trading_day", *pure]].copy()
    state["feature_version"] = cfg["feature_version"]
    state["historical_strict_pit"] = 0
    state["vix_feature_lag_sessions"] = int(cfg["vix_source"]["feature_lag_sessions"])
    state["adjusted_close_used"] = 0
    state["price_observation_policy"] = cfg["etf_ingestion"]["feature_observation_policy"]
    state["action_observation_policy"] = cfg["etf_ingestion"]["action_observation_policy"]
    state["return_convention"] = cfg["etf_ingestion"]["return_convention"]
    state["cash_action_availability_basis"] = cfg["etf_ingestion"]["cash_action_availability_basis"]
    state["vix_provider_non_equity_rows_excluded"] = excluded_vix_provider_rows
    state["vix_feature_session_basis"] = "XNYS_sessions_only"

    output_db.parent.mkdir(parents=True, exist_ok=True)
    if output_db.exists():
        output_db.unlink()
    with sqlite3.connect(output_db) as conn:
        state.to_sql("market_financial_conditions_v0052", conn, index=False, if_exists="replace")
        conn.execute("CREATE UNIQUE INDEX idx_fincond_v0052_day ON market_financial_conditions_v0052(trading_day)")
        conn.commit()

    finite = np.isfinite(state[pure].to_numpy(float)).all(axis=1)
    return {
        "status": "PASS",
        "rows": int(len(state)),
        "pure_external_features": len(pure),
        "complete_feature_rows": int(finite.sum()),
        "first_day": str(state["trading_day"].min()),
        "last_day": str(state["trading_day"].max()),
        "first_complete_day": None if not finite.any() else str(state.loc[finite, "trading_day"].iloc[0]),
        "strict_historical_pit": False,
        "vix_lag_sessions": 1,
        "adjusted_close_used": False,
        "return_convention": cfg["etf_ingestion"]["return_convention"],
        "vix_provider_rows": int(len(vix_provider)),
        "vix_model_eligible_equity_session_rows": int(len(vix)),
        "vix_provider_non_equity_rows_excluded": excluded_vix_provider_rows,
        "vix_feature_session_basis": "XNYS_sessions_only",
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    a = p.parse_args()
    print(json.dumps(build(a.config), indent=2))
