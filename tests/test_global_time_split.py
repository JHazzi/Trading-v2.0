import pandas as pd

from evaluation.backtest.global_time_split import global_time_split


def test_global_cutoff_has_no_temporal_overlap():
    df = pd.DataFrame(
        {
            "asset_id": [1, 2, 1, 2],
            "origin_time": [
                "2026-01-01T10:00:00Z",
                "2026-01-01T10:00:00Z",
                "2026-01-01T11:00:00Z",
                "2026-01-01T11:00:00Z",
            ],
            "return_pct": [0.1, -0.1, 0.2, -0.2],
        }
    )

    split = global_time_split(df, test_fraction=0.5)

    assert split.train["origin_time"].max() < split.test["origin_time"].min()
    assert len(split.train) == 2
    assert len(split.test) == 2
