from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PATCHES = {
    "docs/RESEARCH_STATUS.md": """
<!-- MARKET_DISTRIBUTIONAL_V008_V001_START -->
## Distributional Market Brain V007 close / V008 preregistration — 2026-08-27

V007 Adaptive Asymmetric Asset Scale is closed as a negative developmental result:
- all H1/H3/H5/H10 horizon gates failed;
- zero horizons had a positive point estimate versus the `vol63_scaled_empirical` primary reference;
- the candidate remained better than the unconditional empirical distribution but did not add information beyond the stronger vol63 reference;
- calibration was worse than vol63 at all four horizons.

Interpretation: conditional dispersion information is real, but hand-parameterizing asset anchors plus vol20/vol63 asymmetric scaling did not add reproducible information beyond the strong long-memory empirical scale baseline.

V008 tests a different question. Terminal return is standardized by causal `asset_vol_63d_pct`, and shallow/medium regularized HGB quantile learners predict the remaining conditional residual distribution from the frozen endogenous Core V003 Market State. The primary reference is a `vol63` empirical distribution given the same 126-origin-day train-only standardized quantile recalibration as the candidate. Scale-only and own-state learners are diagnostics and cannot rescue a failed full-endogenous primary after results.

If V008 fails, the next research action is information enrichment, not additional endogenous model capacity.
<!-- MARKET_DISTRIBUTIONAL_V008_V001_END -->
""",
    "docs/RESEARCH_DECISIONS.md": """
<!-- MARKET_DISTRIBUTIONAL_V008_V001_START -->
## 2026-08-27 — Stop handcrafted scale tuning; test conditional information sufficiency

Decision: reject V007 without post-result tuning and preregister V008 Conditional Residual Quantiles.

Rationale: V007 lost to vol63 at all horizons, so another handcrafted volatility formula would be post-hoc specification search. V008 instead asks whether the existing causal endogenous Market State contains information about future standardized-return shape after a strong vol63 scale and recent train-only recalibration are already accounted for.

The full endogenous feature family is primary. Same-capacity scale-only and own-state variants are diagnostics only. If the primary fails, no diagnostic feature family is auto-promoted; a later experiment must preregister any narrower model. A broad V008 failure is interpreted as evidence that the information state is insufficient beyond calibrated volatility, not as permission to increase tree depth, add a neural network, or tune more windows.
<!-- MARKET_DISTRIBUTIONAL_V008_V001_END -->
""",
    "docs/ROADMAP.md": """
<!-- MARKET_DISTRIBUTIONAL_V008_V001_START -->
## Learned Distributional Market Brain V008 — conditional residual information gate

Sequence after V007:
```text
V006 empirical volatility scale                 SUPPORTED
V006.1 robustness / vol63 sensitivity           COMPLETE
V007 handcrafted adaptive asymmetric scale      REJECTED
V008 conditional residual quantile learner      NEXT
```

V008 freezes `vol63_recent_calibrated` as the primary reference. Candidate and reference receive the same recent train-only calibration opportunity. Hyperparameter profile selection is nested temporally. The Core V003 feature schema is resolved without outcomes during `--stage plan`, persisted as `resolved_feature_manifest.json`, and must be committed before benchmarking.

Decision branch:
- if V008 beats the calibrated vol63 reference with acceptable calibration across horizons, retain the learned endogenous distributional Market Brain and test new information blocks incrementally;
- if V008 does not, stop increasing endogenous learner capacity and prioritize causally versioned information that a professional investor would actually use but Core V003 lacks: expectations/revisions, option-implied risk, fundamentals/valuation, positioning/flows and richer event surprise, one block at a time;
- Event Brain, graph, trajectories and trading remain downstream of a credible base distribution.
<!-- MARKET_DISTRIBUTIONAL_V008_V001_END -->
""",
    "docs/EXPERIMENTS.md": """
<!-- MARKET_DISTRIBUTIONAL_V008_V001_START -->
## Market Distributional V008 — Conditional Residual Quantiles

- Version: `market_brain_distributional_v008_conditional_residual_quantiles_v001`
- Target: H1/H3/H5/H10 terminal `return_pct` distribution.
- Residualization: `(return - development_train_median) / asset_vol_63d_pct` on positive-scale rows.
- Learner: `HistGradientBoostingRegressor(loss=quantile)` for q05/q25/q50/q75/q95.
- Capacity: two frozen regularized profiles; selection only in nested temporal validation.
- Training weights: equal total weight per origin trading day.
- Calibration: final 126 origin days of each outer-train period, purged from model development; quantile-specific weighted residual shifts in standardized space.
- Primary reference: equivalently recent-calibrated vol63 empirical distribution.
- Primary candidate: full endogenous Core V003 state.
- Diagnostics: same selected capacity on scale-only and own-state features; no post-hoc rescue.
- Proper score: equal-origin-day mean pinball; 5/10/20-day moving-block bootstrap; calibration/coverage always reported.
- Claim boundary: developmental current-cohort historical reconstruction, not strict PIT, not direction/profitability/path/production evidence.
<!-- MARKET_DISTRIBUTIONAL_V008_V001_END -->
""",
}


def apply_patch(path: Path, block: str, apply: bool) -> bool:
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    marker = block.strip().splitlines()[0]
    if marker in text:
        return False
    if apply:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text.rstrip() + "\n\n" + block.strip() + "\n", encoding="utf-8")
    return True


def main() -> None:
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true")
    g.add_argument("--apply", action="store_true")
    args = p.parse_args()
    changed = []
    for rel, block in PATCHES.items():
        if apply_patch(ROOT / rel, block, args.apply):
            changed.append(rel)
    print({"mode": "apply" if args.apply else "check", "files_changed": len(changed), "files": changed})


if __name__ == "__main__":
    main()
