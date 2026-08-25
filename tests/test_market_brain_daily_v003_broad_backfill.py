from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd

from ingestion.prices.yahoo_daily_broad_v003 import (
    _blank_manifest,
    discover_one,
    load_config,
    manifest_summary,
    plan_audit,
    preflight,
    resolve_exchange_from_candidates,
)


class FakeProvider:
    __version__ = "test"

    class config:
        class debug:
            hide_exceptions = False


class FakeFastInfo(dict):
    pass


class FakeTicker:
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.fast_info = FakeFastInfo({"exchange": "NMS"})

    def history(self, **kwargs):
        assert kwargs["auto_adjust"] is False
        assert kwargs["actions"] is True
        idx = pd.to_datetime(
            ["2020-01-02", "2020-01-03", "2020-01-06"]
        ).tz_localize("UTC")
        return pd.DataFrame(
            {
                "Open": [1, 1, 1],
                "High": [1, 1, 1],
                "Low": [1, 1, 1],
                "Close": [1, 1, 1],
                "Volume": [1, 1, 1],
            },
            index=idx,
        )

    def get_history_metadata(self):
        return {
            "exchangeName": "NMS",
            "instrumentType": "EQUITY",
        }


def fake_ticker_factory(symbol: str):
    return FakeTicker(symbol)


def _db(path: Path) -> None:
    with sqlite3.connect(path) as c:
        c.executescript(
            """
            CREATE TABLE assets(
                asset_id INTEGER PRIMARY KEY,
                ticker TEXT,
                asset_type TEXT,
                sector TEXT,
                exchange TEXT,
                active INTEGER
            );
            CREATE TABLE price_quality_runs(
                quality_run_id TEXT PRIMARY KEY
            );
            CREATE TABLE price_quality_results(
                quality_result_id TEXT PRIMARY KEY,
                quality_run_id TEXT,
                check_name TEXT,
                check_status TEXT,
                observed_value REAL,
                expected_value REAL
            );
            CREATE TABLE q(
                asset_id INTEGER,
                trading_day TEXT
            );
            CREATE VIEW daily_price_quality_gated_observations_v001
            AS SELECT * FROM q;
            """
        )
        for i, ticker in enumerate(("AAA", "BBB", "CCC"), start=1):
            c.execute(
                """
                INSERT INTO assets
                VALUES(?, ?, 'equity', 'Tech', NULL, 1)
                """,
                (i, ticker),
            )
        for j in range(2500):
            c.execute(
                "INSERT INTO q VALUES(1, ?)",
                (f"2020-{j:04d}",),
            )


def test_exchange_metadata_maps_to_canonical():
    exchange, rejected = resolve_exchange_from_candidates(["NMS"])
    assert exchange == "XNAS"
    assert rejected == []


def test_discovery_is_listing_aware_and_does_not_persist():
    result = discover_one(
        ticker="AAA",
        provider_symbol="AAA",
        requested_start="2016-08-25",
        requested_end="2026-08-25",
        existing_exchange=None,
        exchange_override=None,
        ticker_factory=fake_ticker_factory,
        provider_module=FakeProvider,
    )
    assert result["status"] == "READY"
    assert result["exchange"] == "XNAS"
    assert result["discovered_first_day"] == "2020-01-02"
    assert "not persisted" in result["note"]


def test_preflight_skips_already_broad_asset(tmp_path: Path):
    db = tmp_path / "x.db"
    _db(db)
    config = {
        "requested_start": "2016-08-25",
        "requested_end_exclusive": "2026-08-25",
        "minimum_existing_days_to_skip": 2500,
    }
    result = preflight(db, config)
    assert result["active_equities"] == 3
    assert result["existing_ready_assets"] == 1
    assert result["pending_assets"] == 2
    assert result["assets_missing_exchange_metadata"] == 3


def test_manifest_summary_never_selects_best_subset():
    manifest = {
        "manifest_version": "x",
        "rows": {
            "A": {"ticker": "A", "status": "READY", "exchange": "XNAS"},
            "B": {"ticker": "B", "status": "REVIEW"},
            "C": {"ticker": "C", "status": "ERROR"},
        },
    }
    result = manifest_summary(manifest)
    assert result["status_counts"] == {
        "READY": 1,
        "REVIEW": 1,
        "ERROR": 1,
    }
    assert result["review_tickers"] == ["B"]
    assert result["error_tickers"] == ["C"]


def test_config_freezes_current_cohort_no_proxy_no_macro(tmp_path: Path):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "requested_start": "2016-08-25",
                "requested_end_exclusive": "2026-08-25",
                "include_proxies": False,
                "include_macro": False,
            }
        ),
        encoding="utf-8",
    )
    config = load_config(path)
    assert config["include_proxies"] is False
    assert config["include_macro"] is False


def test_docs_patcher_is_idempotent_marker():
    source = Path(
        "tools/patch_market_v003_backfill_docs_v001.py"
    ).read_text(encoding="utf-8")
    assert "MARKET_V003_BROAD_BACKFILL_V001" in source
    assert "already_applied" in source
    assert "survivorship" in source.lower()


def test_broad_ingestion_has_own_lineage_mode_and_provider_symbol():
    source = Path(
        "ingestion/prices/yahoo_daily_broad_v003.py"
    ).read_text(encoding="utf-8")
    assert "yahoo_daily_broad_v003" in source
    assert "symbol=provider_symbol" in source
    assert "provider_symbol," in source
    assert "result = run_pilot(" not in source
