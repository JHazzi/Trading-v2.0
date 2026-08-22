from __future__ import annotations

import pandas as pd


def test_target_quality_invariants():
    df = pd.DataFrame({
        'return_pct': [1.0, -1.0, 0.0],
        'mfe_pct': [2.0, 0.5, 0.0],
        'mae_pct': [-0.5, -2.0, -0.1],
        'coverage_pct': [100.0, 95.0, 100.0],
        'observed_bars': [10, 10, 10],
        'expected_bars': [10, 10, 10],
    })
    assert (df['mae_pct'] <= df['return_pct']).all()
    assert (df['return_pct'] <= df['mfe_pct']).all()
    assert df['coverage_pct'].between(0, 100).all()
    assert (df['observed_bars'] <= df['expected_bars']).all()
