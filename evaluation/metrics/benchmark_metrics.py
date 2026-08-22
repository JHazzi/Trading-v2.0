from __future__ import annotations

import numpy as np
from sklearn.metrics import mean_absolute_error, root_mean_squared_error


def evaluate_predictions(
    actual: np.ndarray,
    predicted: np.ndarray,
) -> dict:
    actual = np.asarray(actual, dtype=float)
    predicted = np.asarray(predicted, dtype=float)

    if len(actual) != len(predicted):
        raise ValueError("actual y predicted deben tener la misma longitud.")
    if len(actual) == 0:
        raise ValueError("No hay observaciones para evaluar.")

    mae = float(mean_absolute_error(actual, predicted))
    rmse = float(root_mean_squared_error(actual, predicted))

    actual_sign = np.sign(actual)
    pred_sign = np.sign(predicted)

    # Retornos exactamente 0 no se cuentan como direccionalmente correctos.
    mask = (actual != 0) & (predicted != 0)
    if mask.any():
        directional_accuracy = float(
            np.mean(actual_sign[mask] == pred_sign[mask])
        )
    else:
        directional_accuracy = None

    baseline_zero_mae = float(mean_absolute_error(actual, np.zeros_like(actual)))
    improvement_vs_zero = 1.0 - (mae / baseline_zero_mae) if baseline_zero_mae else 0.0

    return {
        "n": int(len(actual)),
        "mae_pct": mae,
        "rmse_pct": rmse,
        "directional_accuracy": directional_accuracy,
        "zero_mae_pct": baseline_zero_mae,
        "improvement_vs_zero_pct": float(improvement_vs_zero),
    }
