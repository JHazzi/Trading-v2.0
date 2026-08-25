from __future__ import annotations

import numpy as np
import pandas as pd

from evaluation.events.audit_v002 import audit_frame
from evaluation.events.walkforward_v002 import build_purged_event_folds
from models.events.dataset_v002 import _context_features


def _history(return_scale: float, sector: str):
    # Build 21 synthetic closes with deterministic drift.
    close = [100.0]
    for _ in range(20):
        close.append(close[-1] * (1.0 + return_scale))
    rows = [
        {
            "day": f"2026-01-{i+1:02d}",
            "close": value,
            "high": value * 1.01,
            "low": value * 0.99,
            "volume": 1000.0 + i,
        }
        for i, value in enumerate(close)
    ]
    return {"sector": sector, "ticker": sector, "rows": rows}


def test_cross_section_is_leave_one_out_and_sector_is_peer_only():
    histories = {
        1: _history(0.010, "tech"),
        2: _history(0.020, "tech"),
        3: _history(-0.010, "finance"),
        4: _history(0.000, "finance"),
    }
    result = _context_features(histories, 1)
    assert result is not None
    features, sector = result

    assert sector == "tech"
    assert features["cross_section_peer_count"] == 3.0
    assert features["sector_peer_count"] == 1.0
    assert features["sector_context_fallback"] == 0.0

    # Target 1 cannot be part of the cross-section median.
    peer_1d = np.median([
        features["sector_median_return_1d_pct"],
        -1.0,
        0.0,
    ])
    assert np.isfinite(peer_1d)


def test_purged_folds_group_event_and_purge_overlapping_targets():
    rows = []
    for i in range(60):
        event = f"e{i}"
        day = pd.Timestamp("2025-01-01") + pd.Timedelta(days=i)
        origin = day.date().isoformat()
        target = (day + pd.Timedelta(days=3)).date().isoformat()
        rows.append({
            "event_id": event,
            "event_anchor_day": origin,
            "origin_trading_day": origin,
            "target_trading_day": target,
        })
        if i % 10 == 0:
            rows.append({
                "event_id": event,
                "event_anchor_day": origin,
                "origin_trading_day":
                    (day + pd.Timedelta(days=1)).date().isoformat(),
                "target_trading_day":
                    (day + pd.Timedelta(days=4)).date().isoformat(),
            })
    frame = pd.DataFrame(rows)
    folds = build_purged_event_folds(
        frame,
        n_folds=3,
        initial_fraction=0.45,
        min_train_rows=15,
        min_test_rows=5,
    )
    assert len(folds) >= 2

    for fold in folds:
        train = frame.loc[list(fold.train_index)]
        test = frame.loc[list(fold.test_index)]
        assert set(train.event_id).isdisjoint(set(test.event_id))
        assert train.target_trading_day.max() < fold.first_test_anchor_day


def test_audit_rejects_concentrated_dataset():
    frame = pd.DataFrame({
        "asset_id": [1] * 190 + [2] * 10,
        "event_type": ["earnings"] * 200,
        "event_id": [f"e{i}" for i in range(200)],
        "origin_trading_day": pd.date_range(
            "2024-01-01",
            periods=200,
            freq="3D",
        ).date.astype(str),
        "sector_context_fallback": [0.0] * 200,
    })
    result = audit_frame(frame)
    assert result["status"] == "FAIL"
    assert any("assets<" in x for x in result["failures"])
    assert any("max_asset_share" in x for x in result["failures"])
