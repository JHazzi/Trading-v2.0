from __future__ import annotations

import numpy as np
import pandas as pd

from models.market.baseline_v001 import MarketBaselineV001


def test_baseline_outputs_ordered_quantiles():
    rng = np.random.default_rng(42)
    X = pd.DataFrame(rng.normal(size=(80, 4)), columns=list('abcd'))
    y = 0.4 * X['a'] - 0.2 * X['b'] + rng.normal(0, 0.1, size=80)
    model = MarketBaselineV001(n_estimators=60, min_samples_leaf=2)
    model.fit(X.iloc[:60], y.iloc[:60], list(X.columns), 300)
    pred = model.predict_distribution(X.iloc[60:])
    assert pred
    for p in pred:
        assert p.q05 <= p.q25 <= p.q50 <= p.q75 <= p.q95
        assert 0.0 <= p.probability_positive_raw <= 1.0
