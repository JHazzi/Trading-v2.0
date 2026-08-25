from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

from features.market.daily_v003_core import (
    _future,
    compute_context_features,
    compute_labels,
    compute_own_features,
)


def cfg():
    return {
        "feature_version": "test_f",
        "label_version": "test_l",
        "minimum_own_history_days": 253,
        "minimum_cross_section_peers_ex_target": 50,
        "minimum_sector_peers_ex_target": 3,
        "horizons_sessions": [1, 3, 5, 10],
    }


def synthetic_prices(assets=55, days=270):
    dates = pd.bdate_range("2020-01-01", periods=days)
    rows = []
    for a in range(assets):
        for i, day in enumerate(dates):
            close = 100 + a*0.1 + i*0.03 + 0.2*np.sin(i/9+a)
            rows.append({
                "asset_id": a+1,
                "ticker": f"T{a+1:03d}",
                "sector": f"S{a%5}",
                "trading_day": day.date().isoformat(),
                "state_time": day.date().isoformat()+"T21:00:00+00:00",
                "open": close-0.1,
                "high": close+0.5,
                "low": close-0.5,
                "close": close,
                "volume": 1_000_000+i+a,
            })
    return pd.DataFrame(rows)


def action_db(path: Path):
    with sqlite3.connect(path) as conn:
        conn.executescript("""
        CREATE TABLE corporate_action_versions(
            corporate_action_version_id TEXT PRIMARY KEY,
            is_present INTEGER
        );
        CREATE TABLE corporate_action_observations(
            action_observation_id TEXT PRIMARY KEY,
            corporate_action_version_id TEXT,
            asset_id INTEGER,
            effective_trading_day TEXT,
            action_type TEXT,
            observation_sequence INTEGER,
            observed_at TEXT
        );
        """)


def test_future_h1_std_is_population_zero():
    s = pd.Series([np.nan, 1.0, -2.0, 4.0])
    assets = pd.Series([1,1,1,1])
    out = _future(s, assets, 1, "std")
    assert np.allclose(out.iloc[:3].to_numpy(float), 0.0)
    assert np.isnan(out.iloc[-1])


def test_h1_labels_are_usable_and_have_zero_path_vol(tmp_path: Path):
    prices = synthetic_prices()
    own = compute_own_features(prices, cfg())
    states = compute_context_features(own, cfg())
    db = tmp_path / "a.db"
    action_db(db)
    labels = compute_labels(own, states, db, cfg())

    h1 = labels[labels.horizon_sessions == 1]
    usable = h1[h1.label_status == "usable"]

    # One terminal row per asset cannot have a future H1 target.
    assert len(usable) > 0.90*len(h1)
    assert np.allclose(usable.realized_path_vol_pct.to_numpy(float), 0.0)


def test_audit_has_h1_and_horizon_coverage_guards():
    src = Path("evaluation/market/daily_v003_core_audit.py").read_text(
        encoding="utf-8"
    )
    assert "h1_has_no_usable_labels" in src
    assert "h1_path_vol_not_zero" in src
    assert "usable_fraction_below_50pct" in src
    assert "horizon_set_mismatch" in src
