from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from ingestion.prices.yahoo_daily_refresh_v009 import (
    REGULAR_CLOSE_FALLBACK,
    apply_regular_close_fallback,
)
from ingestion.prices.yahoo_daily_v1 import canonical_provider_payload
from pipeline import market_brain_daily_refresh_v009 as refresh


def origin_frame(close: float = float("nan")) -> pd.DataFrame:
    index = pd.DatetimeIndex(
        ["2026-08-28 00:00:00-04:00"], name="Date"
    )
    return pd.DataFrame(
        {
            "Open": [317.088],
            "High": [322.37],
            "Low": [315.45],
            "Close": [close],
            "Adj Close": [float("nan")],
            "Volume": [38_500_185],
            "Dividends": [0.0],
            "Stock Splits": [0.0],
        },
        index=index,
    )


def test_refresh_config_is_bound_to_frozen_v009() -> None:
    cfg = refresh.load_config()
    assert cfg["version"] == "market_brain_daily_refresh_v009_v002"
    assert cfg["source_asof_contract"] == "daily_price_asof_v2"
    assert cfg["not_before_origin_day"] == "2026-08-28"
    assert cfg["provider_settlement_minutes_after_close"] == 20
    assert (
        cfg["regular_market_close_fallback_version"]
        == REGULAR_CLOSE_FALLBACK
    )
    assert cfg["refit_v009_after_refresh"] is False
    assert cfg["minimum_source_assets_on_origin"] >= 490
    assert cfg["minimum_core_states_on_origin"] >= 490
    assert cfg["ohlc_envelope_repair_version"] == "ohlc_envelope_repair_v001"
    assert cfg["ohlc_envelope_repair_minimum_invalid_observations"] >= 2
    assert cfg["ohlc_envelope_repair_maximum_relative_expansion_pct"] == 2.0
    assert cfg["ohlc_envelope_repair_preserved_fields"] == [
        "open", "close", "volume", "adjusted_close"
    ]


def test_ohlc_envelope_repair_is_minimal_and_preserves_open_close() -> None:
    repaired = refresh.ohlc_envelope_repair(
        1032.599976,
        1029.925293,
        1022.620117,
        1025.900024,
        2.0,
    )
    assert repaired["open"] == pytest.approx(1032.599976)
    assert repaired["close"] == pytest.approx(1025.900024)
    assert repaired["repaired_high"] == pytest.approx(1032.599976)
    assert repaired["repaired_low"] == pytest.approx(1022.620117)
    assert repaired["upper_expansion"] == pytest.approx(
        1032.599976 - 1029.925293
    )
    assert repaired["lower_expansion"] == 0.0


def test_ohlc_envelope_repair_rejects_large_or_unneeded_change() -> None:
    with pytest.raises(ValueError, match="expansion cap"):
        refresh.ohlc_envelope_repair(120.0, 100.0, 90.0, 95.0, 2.0)
    with pytest.raises(ValueError, match="already a valid envelope"):
        refresh.ohlc_envelope_repair(95.0, 100.0, 90.0, 96.0, 2.0)


def test_derived_provider_payload_exposes_repair_lineage() -> None:
    frame = origin_frame(319.70)
    payload, _ = canonical_provider_payload(
        frame,
        symbol="AAPL",
        requested_start="2026-08-28",
        requested_end="2026-08-29",
        provider_library_version="ohlc_envelope_repair_v001",
        exchange="XNAS",
        calendar_name="XNAS",
        lineage_kind="derived_operational_repair",
        provider_library_name="quant_market_ai",
        derivation={"source_price_observation_id": "source"},
    )
    import json

    decoded = json.loads(payload)
    assert decoded["lineage_kind"] == "derived_operational_repair"
    assert decoded["provider_library"]["name"] == "quant_market_ai"
    assert decoded["derivation"] == {
        "source_price_observation_id": "source"
    }


def test_repair_daily_index_is_timezone_aware() -> None:
    index = pd.DatetimeIndex(
        [pd.Timestamp("2026-08-31", tz="America/New_York")],
        name="Date",
    )
    frame = pd.DataFrame(
        {
            "Open": [10.1], "High": [10.1], "Low": [9.9],
            "Close": [10.0], "Adj Close": [10.0], "Volume": [100],
        },
        index=index,
    )
    _, rows = canonical_provider_payload(
        frame,
        symbol="TEST",
        requested_start="2026-08-31",
        requested_end="2026-09-01",
        provider_library_version="ohlc_envelope_repair_v001",
        exchange="XNYS",
        calendar_name="XNYS",
    )
    assert rows[0]["trading_day"] == "2026-08-31"


def test_repair_gate_is_required_from_effective_day(tmp_path) -> None:
    gate = refresh.validate_ohlc_repair_gate(
        refresh.DEFAULT_CONFIG,
        tmp_path / "source.db",
        tmp_path / "registry.db",
        tmp_path / "reports",
        "2026-08-31",
    )
    assert gate["status"] == "MISSING_REQUIRED_OPERATIONAL_AMENDMENT"


def test_origin_clock_enforces_provider_settlement_delay() -> None:
    waiting = refresh.origin_clock(
        "2026-08-28",
        {"XNYS", "XNAS", "BATS"},
        20,
        datetime(2026, 8, 28, 20, 19, 59, tzinfo=timezone.utc),
    )
    ready = refresh.origin_clock(
        "2026-08-28",
        {"XNYS", "XNAS", "BATS"},
        20,
        datetime(2026, 8, 28, 20, 20, 0, tzinfo=timezone.utc),
    )
    assert waiting["latest_close_utc"] == "2026-08-28T20:00:00+00:00"
    assert waiting["earliest_acquire_utc"] == "2026-08-28T20:20:00+00:00"
    assert waiting["status"] == "WAITING_FOR_CLOSE"
    assert ready["status"] == "READY"


def test_regular_close_fallback_uses_only_same_session_regular_price() -> None:
    frame, evidence = apply_regular_close_fallback(
        origin_frame(),
        {
            "regularMarketPrice": 319.70,
            "regularMarketTime": datetime(
                2026, 8, 28, 20, 0, 1, tzinfo=timezone.utc
            ),
            "postMarketPrice": 319.90,
        },
        origin_day="2026-08-28",
        exchange="XNAS",
        retrieved_at="2026-08-29T04:18:29+00:00",
        maximum_market_time_delay_seconds=300,
    )
    assert frame.iloc[0]["Close"] == pytest.approx(319.70)
    assert np.isnan(frame.iloc[0]["Adj Close"])
    assert frame.iloc[0]["Provider Daily Close"] is np.nan or np.isnan(
        frame.iloc[0]["Provider Daily Close"]
    )
    assert frame.iloc[0]["Close Source"] == REGULAR_CLOSE_FALLBACK
    assert evidence["applied"] is True
    assert evidence["post_market_price_used"] is False
    assert evidence["adjusted_close_filled"] is False


def test_regular_close_fallback_rejects_post_close_time_too_late() -> None:
    with pytest.raises(ValueError, match="outside the allowed close boundary"):
        apply_regular_close_fallback(
            origin_frame(),
            {
                "regularMarketPrice": 319.90,
                "regularMarketTime": datetime(
                    2026, 8, 29, 0, 0, 0, tzinfo=timezone.utc
                ),
            },
            origin_day="2026-08-28",
            exchange="XNAS",
            retrieved_at="2026-08-29T04:18:29+00:00",
            maximum_market_time_delay_seconds=300,
        )


def test_complete_daily_close_is_not_overwritten() -> None:
    frame, evidence = apply_regular_close_fallback(
        origin_frame(319.70),
        {
            "regularMarketPrice": 999.0,
            "regularMarketTime": datetime(
                2026, 8, 28, 20, 0, 1, tzinfo=timezone.utc
            ),
        },
        origin_day="2026-08-28",
        exchange="XNAS",
        retrieved_at="2026-08-29T04:18:29+00:00",
        maximum_market_time_delay_seconds=300,
    )
    assert frame.iloc[0]["Close"] == pytest.approx(319.70)
    assert evidence["applied"] is False


def test_refresh_rejects_origin_before_preregistered_start(monkeypatch) -> None:
    cfg = refresh.load_config()
    monkeypatch.setattr(
        refresh,
        "frozen_contract",
        lambda unused: (
            {},
            {"assets": []},
            {"version": cfg["supported_experiment_version"]},
        ),
    )
    with pytest.raises(
        RuntimeError, match="refresh origin predates frozen V009 start"
    ):
        refresh.refresh_plan(
            refresh.DEFAULT_CONFIG,
            refresh.ROOT / "unused-source.db",
            refresh.ROOT / "unused-registry.db",
            "2026-08-27",
        )


def test_cli_summary_preserves_report_but_compacts_terminal() -> None:
    payload = {
        "status": "WAITING_FOR_CLOSE",
        "assets": [
            {"status": "PENDING"},
            {"status": "PENDING"},
            {"status": "ALREADY_PRESENT"},
        ],
        "source_audit": {
            "status": "PASS",
            "source_assets": 497,
            "observation_clock": [{"asset_id": 1}],
        },
    }
    compact = refresh.cli_summary(payload)
    assert "assets" not in compact
    assert compact["asset_status_counts"] == {
        "PENDING": 2,
        "ALREADY_PRESENT": 1,
    }
    assert "observation_clock" not in compact["source_audit"]
    assert "assets" in payload
    assert "observation_clock" in payload["source_audit"]

def test_migration_023_backdates_only_first_quality_eligible(
    tmp_path,
) -> None:
    import sqlite3

    db = tmp_path / "source.db"
    with sqlite3.connect(db) as conn:
        conn.executescript(
            """
            CREATE TABLE schema_migrations(
              version TEXT PRIMARY KEY,name TEXT,applied_at TEXT
            );
            CREATE TABLE daily_price_asof_configs(
              asof_contract_version TEXT,mode TEXT,cutoff_column TEXT,
              required_quality_version TEXT,required_quality_status TEXT,
              max_failed_checks INTEGER,selection_point_in_time_verified INTEGER,
              adjusted_close_role TEXT,disclosure TEXT,
              configuration_json TEXT,
              PRIMARY KEY(asof_contract_version,mode)
            );
            CREATE TABLE daily_price_quality_gated_observations_v001(
              source_id TEXT,asset_id INTEGER,interval TEXT,trading_day TEXT,
              observation_sequence INTEGER,observed_at TEXT,
              price_observation_id TEXT,bar_end_utc TEXT,available_at TEXT,
              availability_basis TEXT
            );
            INSERT INTO daily_price_quality_gated_observations_v001 VALUES
              ('yahoo',1,'1d','2026-08-28',2,
               '2026-08-29T04:00:00+00:00','approved_first',
               '2026-08-28T20:00:00+00:00',
               '2026-08-29T04:00:00+00:00','retrieval_time_revision'),
              ('yahoo',1,'1d','2026-08-28',3,
               '2026-08-29T05:00:00+00:00','approved_revision',
               '2026-08-28T20:00:00+00:00',
               '2026-08-29T05:00:00+00:00','retrieval_time_revision');
            """
        )
        migration = (
            refresh.ROOT
            / "database/migrations/023_daily_price_first_quality_eligible.sql"
        ).read_text(encoding="utf-8")
        conn.executescript(migration)
        rows = conn.execute(
            """
            SELECT price_observation_id,causal_available_at,
                   causal_availability_basis,quality_eligible_rank
            FROM daily_price_quality_gated_observations_v002
            ORDER BY quality_eligible_rank
            """
        ).fetchall()
    assert rows == [
        (
            "approved_first",
            "2026-08-28T20:00:00+00:00",
            "first_quality_eligible_session_close_assumption",
            1,
        ),
        (
            "approved_revision",
            "2026-08-29T05:00:00+00:00",
            "retrieval_time_revision",
            2,
        ),
    ]
