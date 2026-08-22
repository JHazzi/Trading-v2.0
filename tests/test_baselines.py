import numpy as np
import pandas as pd

from evaluation.baselines.baselines import (
    asset_mean_prediction,
    zero_prediction,
)


def test_zero_prediction():
    train = pd.DataFrame({"asset_id": [1, 2], "return_pct": [1.0, -1.0]})
    test = pd.DataFrame({"asset_id": [1, 2, 3]})
    np.testing.assert_array_equal(
        zero_prediction(train, test),
        np.array([0.0, 0.0, 0.0]),
    )


def test_asset_mean_uses_train_only_and_fallback():
    train = pd.DataFrame(
        {
            "asset_id": [1, 1, 2],
            "return_pct": [1.0, 3.0, -2.0],
        }
    )
    test = pd.DataFrame({"asset_id": [1, 2, 3]})

    pred = asset_mean_prediction(train, test)
    np.testing.assert_allclose(
        pred,
        np.array([2.0, -2.0, 2.0 / 3.0]),
    )
