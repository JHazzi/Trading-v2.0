from __future__ import annotations

import pandas as pd

from features.market.market_state_builder import build_asset_state


def test_market_state_has_expected_features():
    n = 420
    ts = pd.date_range("2026-01-01 14:30:00+00:00", periods=n, freq="min")
    base = pd.Series(range(n), dtype=float) + 100.0
    df = pd.DataFrame({
        "timestamp": ts,
        "open": base,
        "high": base + 0.2,
        "low": base - 0.2,
        "close": base,
        "volume": 1000.0,
    })
    out = build_asset_state(df)
    assert "return_60m_pct" in out.columns
    assert "atr_14_pct" in out.columns
    assert "rsi_14" in out.columns
    assert out.iloc[-1]["return_60m_pct"] > 0
