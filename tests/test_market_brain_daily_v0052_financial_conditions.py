from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from features.market.daily_v0052_financial_conditions import (
    rolling_zscore, compound_return, total_return_from_close_and_cash,
)
from ingestion.market_reference.cboe_vix_daily_v001 import parse_csv
from ingestion.market_reference.financial_condition_etfs_v001 import windows


def cfg():
    return json.loads(
        Path("config/market_brain_daily_v0052_financial_conditions.json").read_text(encoding="utf-8")
    )


def test_contract_does_not_stack_rejected_v0051():
    c = cfg()
    assert c["rejected_not_stacked"] == "V005.1 SPY_QQQ_IWM"
    assert "SPY_QQQ_IWM_reentry" in c["deferred"]


def test_primary_external_feature_count_is_20():
    c = cfg()
    pure = sum(c["pure_external_features"].values(), [])
    assert len(pure) == 19
    assert len(c["interaction_features"]) == 1
    assert len(pure) + len(c["interaction_features"]) == 20


def test_vix_is_explicitly_lagged_one_session():
    c = cfg()
    assert c["vix_source"]["feature_lag_sessions"] == 1
    assert c["state_clock"]["same_day_vix_close_allowed"] is False


def test_adjusted_close_forbidden_and_total_return_formula_preregistered():
    c = cfg()
    assert c["etf_ingestion"]["use_adjusted_close_for_features"] is False
    assert c["etf_ingestion"]["return_convention"] == "close_plus_effective_cash_distribution_compounded"


def test_total_return_uses_today_cash_only():
    close = pd.Series([100.0, 99.0, 100.0])
    cash = pd.Series([0.0, 1.0, 0.0])
    r = total_return_from_close_and_cash(close, cash)
    assert np.isnan(r.iloc[0])
    assert abs(r.iloc[1]) < 1e-12
    assert abs(r.iloc[2] - (100.0 / 99.0 - 1.0) * 100.0) < 1e-12


def test_compound_return_is_causal():
    r = pd.Series([1.0, 2.0, 3.0])
    c = compound_return(r, 2)
    assert np.isnan(c.iloc[0])
    expected = ((1.02 * 1.03) - 1.0) * 100.0
    assert abs(c.iloc[2] - expected) < 1e-12


def test_zscore_needs_full_history():
    x = pd.Series(np.arange(70, dtype=float))
    z = rolling_zscore(x, 63)
    assert z.iloc[:62].isna().all()
    assert np.isfinite(z.iloc[62])


def test_cboe_parser_accepts_case_and_date_formats():
    raw = (
        "DATE,OPEN,HIGH,LOW,CLOSE\n"
        "01/02/2020,13,15,12,14\n"
        "2020-01-03,14,16,13,15\n"
    ).encode()
    rows = parse_csv(raw)
    assert rows[0]["trading_day"] == "2020-01-02"
    assert rows[1]["close"] == 15.0


def test_chunk_windows_cover_without_overlap():
    w = list(windows("2020-01-01", "2021-01-01", 100))
    assert w[0][0] == "2020-01-01"
    assert w[-1][1] == "2021-01-01"
    for a, b in zip(w, w[1:]):
        assert a[1] == b[0]


def test_reference_exchanges_are_preregistered():
    c = cfg()
    assert c["reference_assets"]["SHY"]["exchange"] == "XNAS"
    assert c["reference_assets"]["IEF"]["exchange"] == "XNAS"
    assert c["reference_assets"]["TLT"]["exchange"] == "XNAS"
    assert c["reference_assets"]["HYG"]["exchange"] == "ARCX"
    assert c["reference_assets"]["LQD"]["exchange"] == "ARCX"


def test_yahoo_arca_patch_is_idempotent_and_calendar_proxy_explicit():
    src = Path("tools/patch_yahoo_daily_arca_support_v001.py").read_text(encoding="utf-8")
    assert '"NYSE ARCA": "ARCX"' in src
    assert '"ARCX": "XNYS"' in src
    assert "already_applied" in src


def test_secondary_ablations_cannot_rescue_primary():
    c = cfg()
    b = c["benchmark"]
    assert b["primary_candidate"] == "full_financial_conditions"
    assert b["do_not_rescue_primary_with_secondary_ablation"] is True
    assert b["do_not_tune_after_results"] is True


def test_benchmark_source_has_parity_and_same_row_guards():
    src = Path("models/market/daily_v0052_financial_conditions_benchmark.py").read_text(encoding="utf-8")
    assert "V004 replay parity failure" in src
    assert "V005.2 OOS state set differs from V004" in src
    assert "SPY_QQQ_IWM_stacked" in src


def test_feature_builder_sql_contract_on_synthetic_databases(tmp_path):
    import sqlite3
    from features.market import daily_v0052_financial_conditions as mod

    main_db = tmp_path / "main.db"
    vix_db = tmp_path / "vix.db"
    out_db = tmp_path / "state.db"
    math_db = tmp_path / "math.db"

    dates = pd.bdate_range("2019-01-02", periods=320).strftime("%Y-%m-%d").tolist()
    tickers = ["SHY", "IEF", "TLT", "HYG", "LQD"]

    with sqlite3.connect(main_db) as conn:
        conn.executescript("""
        CREATE TABLE assets(asset_id INTEGER PRIMARY KEY,ticker TEXT,asset_type TEXT,exchange TEXT,active INTEGER,source TEXT);
        CREATE TABLE price_bar_versions(price_bar_version_id TEXT PRIMARY KEY,open REAL,high REAL,low REAL,close REAL,volume REAL);
        CREATE TABLE price_bar_observations(
          price_observation_id TEXT PRIMARY KEY,price_bar_version_id TEXT,source_id TEXT,
          asset_id INTEGER,interval TEXT,trading_day TEXT,available_at TEXT,
          availability_basis TEXT,point_in_time_verified INTEGER,observation_sequence INTEGER
        );
        CREATE TABLE corporate_action_versions(
          corporate_action_version_id TEXT PRIMARY KEY,is_present INTEGER,raw_value REAL
        );
        CREATE TABLE corporate_action_observations(
          action_observation_id TEXT PRIMARY KEY,corporate_action_version_id TEXT,
          source_id TEXT,asset_id INTEGER,effective_trading_day TEXT,action_type TEXT,
          observation_sequence INTEGER
        );
        """)
        for aid, ticker in enumerate(tickers, start=1):
            conn.execute("INSERT INTO assets VALUES (?,?,?,?,?,?)", (aid,ticker,"etf_reference","XNAS",0,"test"))
            for i, day in enumerate(dates):
                close = 100.0 + aid * 2.0 + 0.03 * i + np.sin(i / 10.0)
                vid = f"{ticker}_v_{i}"
                oid = f"{ticker}_o_{i}"
                conn.execute("INSERT INTO price_bar_versions VALUES (?,?,?,?,?,?)", (vid,close,close+1,close-1,close,1000+i))
                conn.execute(
                    "INSERT INTO price_bar_observations VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (oid,vid,"yahoo_finance",aid,"1d",day,day+"T21:00:00+00:00","session_close_backfill_assumption",0,1),
                )
                if i and i % 30 == 0:
                    avid = f"{ticker}_av_{i}"
                    aoid = f"{ticker}_ao_{i}"
                    conn.execute("INSERT INTO corporate_action_versions VALUES (?,?,?)", (avid,1,0.25))
                    conn.execute(
                        "INSERT INTO corporate_action_observations VALUES (?,?,?,?,?,?,?)",
                        (aoid,avid,"yahoo_finance",aid,day,"dividend",1),
                    )
        conn.commit()

    with sqlite3.connect(vix_db) as conn:
        conn.executescript("""
        CREATE TABLE global_reference_versions(version_id TEXT PRIMARY KEY,open REAL,high REAL,low REAL,close REAL);
        CREATE TABLE global_reference_observations(
          observation_id TEXT PRIMARY KEY,version_id TEXT,source_id TEXT,symbol TEXT,
          trading_day TEXT,available_at TEXT,availability_basis TEXT,
          point_in_time_verified INTEGER,observation_sequence INTEGER
        );
        """)
        for i, day in enumerate(dates):
            value = 15.0 + 2.0 * np.sin(i / 20.0)
            vid = f"vix_v_{i}"
            conn.execute("INSERT INTO global_reference_versions VALUES (?,?,?,?,?)", (vid,value,value+1,value-1,value))
            conn.execute(
                "INSERT INTO global_reference_observations VALUES (?,?,?,?,?,?,?,?,?)",
                (f"vix_o_{i}",vid,"cboe_vix_daily","VIX",day,day+"T21:15:00+00:00","test",0,1),
            )
        conn.commit()

    with sqlite3.connect(math_db) as conn:
        conn.execute("CREATE TABLE v004_market_states(trading_day TEXT)")
        conn.executemany("INSERT INTO v004_market_states VALUES (?)", [(d,) for d in dates[260:]])

    c = cfg()
    c["main_db"] = str(main_db)
    c["vix_db"] = str(vix_db)
    c["state_db"] = str(out_db)
    c["v004_math_db"] = str(math_db)
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(c), encoding="utf-8")

    result = mod.build(config_path)
    assert result["status"] == "PASS"
    assert result["pure_external_features"] == 19
    assert result["vix_lag_sessions"] == 1
    assert result["adjusted_close_used"] is False
    with sqlite3.connect(out_db) as conn:
        frame = pd.read_sql_query("SELECT * FROM market_financial_conditions_v0052", conn)
    assert len(frame) == len(dates)
    assert frame["vix_lag1_close"].iloc[0] != frame["vix_lag1_close"].iloc[0]
    assert np.isfinite(frame["vix_lag1_close"].iloc[-1])
    assert np.isfinite(frame["hyg_minus_lqd_20d_pct"].iloc[-1])


def test_arca_patcher_matches_reviewed_public_shape(tmp_path):
    from tools import patch_yahoo_daily_arca_support_v001 as patch

    target = tmp_path / "yahoo_daily_v1.py"
    target.write_text(
        'EXCHANGE_CANONICAL_MAP = {\n'
        '    "XNYS": "XNYS",\n'
        '    "NYSE": "XNYS",\n'
        '    "NYQ": "XNYS",\n'
        '    "XNAS": "XNAS",\n'
        '    "NASDAQ": "XNAS",\n'
        '    "NMS": "XNAS",\n'
        '    "NGM": "XNAS",\n'
        '    "NCM": "XNAS",\n'
        '}\n'
        'EXCHANGE_CALENDAR_MAP = {\n'
        '    "XNYS": "XNYS",\n'
        '    "XNAS": "XNAS",\n'
        '}\n',
        encoding="utf-8",
    )
    old_target = patch.TARGET
    try:
        patch.TARGET = target
        assert patch.status() == "ready"
        assert patch.apply() == "applied"
        assert patch.status() == "already_applied"
        text = target.read_text(encoding="utf-8")
        assert '"ARCX": "XNYS"' in text
    finally:
        patch.TARGET = old_target
