from __future__ import annotations

from datetime import datetime, timezone

import pytest

from pipeline import market_brain_daily_refresh_v009 as refresh


def test_refresh_config_is_bound_to_frozen_v009() -> None:
    cfg = refresh.load_config()
    assert cfg["not_before_origin_day"] == "2026-08-28"
    assert cfg["provider_settlement_minutes_after_close"] == 20
    assert cfg["refit_v009_after_refresh"] is False
    assert cfg["minimum_source_assets_on_origin"] >= 490
    assert cfg["minimum_core_states_on_origin"] >= 490


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