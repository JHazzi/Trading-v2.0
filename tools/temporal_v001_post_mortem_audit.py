import json
import logging
from pathlib import Path
from typing import Any

import numpy as np


ROOT = Path(__file__).resolve().parents[1]


def _records_values(records) -> np.ndarray:
    ordered = sorted(records, key=lambda x: str(x["origin_trading_day"]))
    return np.asarray([float(x["delta_pct"]) for x in ordered], dtype=float)


def main():
    logging.basicConfig(level=logging.INFO)
    output_dir = ROOT / "reports" / "temporal_distributional_runner_v001"
    
    folds = []
    for i in range(1, 6):
        path = output_dir / "development" / f"fold_{i:02d}.json"
        if not path.exists():
            logging.error(f"Missing {path}")
            return
        folds.append(json.loads(path.read_text(encoding="utf-8")))

    # 1. Loss por fold, tau y cuantil
    loss_by_fold = {}
    for i, f in enumerate(folds, 1):
        loss_by_fold[f"fold_{i}"] = f["point_delta_reference_minus_candidate_pct"]

    loss_by_tau = {}
    for tau in folds[0]["daily_by_tau_candidate_vs_reference"].keys():
        values = _records_values(sum((f["daily_by_tau_candidate_vs_reference"][tau] for f in folds), []))
        loss_by_tau[tau] = float(np.mean(values))

    loss_by_q = {}
    for q in folds[0]["daily_by_quantile_candidate_vs_reference"].keys():
        values = _records_values(sum((f["daily_by_quantile_candidate_vs_reference"][q] for f in folds), []))
        loss_by_q[q] = float(np.mean(values))

    h252_f1 = _records_values(folds[0]["daily_by_tau_candidate_vs_reference"]["252"])

    audit_result = {
        "version": "market_temporal_v001_post_mortem_audit",
        "loss_by_fold": loss_by_fold,
        "loss_by_tau": loss_by_tau,
        "loss_by_quantile": loss_by_q,
        "h252_fold_1_mean_delta": float(np.mean(h252_f1)),
        "h252_fold_1_median_delta": float(np.median(h252_f1)),
        "status": "DRAFT_REQUIRES_FULL_PREDICTION_JOIN",
    }
    
    out_path = output_dir / "post_mortem_audit.json"
    out_path.write_text(json.dumps(audit_result, indent=2))
    logging.info(f"Escrito: {out_path}")


if __name__ == "__main__":
    main()
