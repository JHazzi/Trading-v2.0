# Market Distributional V008 — Conditional Residual Quantiles

## Why V008 exists

V006 established a reproducible conditional-dispersion result. V006.1 showed that 63-session realized volatility is a particularly strong empirical scale reference. V007 then tested a handcrafted asset-anchored, asymmetric nonlinear volatility response and failed every horizon against vol63. V008 therefore stops hand-designing the scale formula.

The question is now an information question:

> After a strong long-memory volatility scale and a fair recent train-only recalibration are already known, does the current causal endogenous Market State contain reproducible information about the remaining future-return distribution?

This is closer to how a professional investor reasons. Price and volatility summarize a large amount of collective information, but a professional also conditions on state, relative behavior and context. V008 asks whether the state already present in Core V003 contains incremental predictive information. It is explicitly allowed to conclude that it does not.

## Mathematical object

For one outer fold and horizon H, split outer training history into development and the final 126 origin days used only for calibration. Let

`m = median(development return)`

and on positive-scale rows

`z = (return_pct - m) / asset_vol_63d_pct`.

The empirical vol63 reference assumes the standardized quantile is constant:

`Q_q(z | X) = Q_q(z)`.

V008 learns instead:

`Q_q(z | X_t)`

with independent regularized histogram gradient boosting quantile heads for q05/q25/q50/q75/q95, followed by deterministic monotone rearrangement.

A matched HGB binary head estimates `P(return > 0)` from the same feature family. Candidate and vol63 reference probabilities are both recalibrated on the same recent 126-origin-day train-only calibration window using weighted isotonic calibration, so Brier score remains a fair secondary diagnostic.

Return quantiles are reconstructed as:

`Q_q(R | X_t) = m + asset_vol_63d_pct_t * Q_q(z | X_t)`.

Rows with nonpositive scale use the unconditional empirical return distribution, consistent with the existing empirical controls.

## Fair recalibration

V006.1 showed temporal calibration drift. Therefore V008 must not win merely because it is allowed to adapt to recent history while the reference is not.

Both the learned candidate and the vol63 empirical reference receive the same final 126-origin-day train-only calibration window. For each quantile, a weighted residual quantile shift is estimated in standardized space and then frozen before the outer test.

The primary comparison is therefore:

`HGB full endogenous + recent calibration`

vs

`vol63 empirical + the same recent calibration opportunity`.

Raw vol63, raw vol20, asset empirical and unconditional empirical remain secondary controls.

## Information decomposition

The full endogenous feature set is the only primary candidate. It is resolved from the frozen Core V003 schema during `--stage plan` using semantic schema rules only; no outcomes are consulted. The resolved manifest is persisted and must be committed before benchmarking.

Two same-capacity diagnostics are also run:

1. scale-only: vol5/vol20/vol63;
2. own-state: asset returns/volatility/range/volume/drawdown/distance families.

The full model adds cross-sectional and sector context. These diagnostics can explain a result but cannot rescue a failed primary after seeing the test.

Interpretation:
- full > calibrated vol63 and full > scale-only: non-scale endogenous state adds information;
- scale-only > calibrated vol63: volatility state has nonlinear information beyond the empirical vol63 rule, but V007's handcrafted form was wrong;
- full does not beat calibrated vol63: current endogenous X has not earned incremental information; do not increase model capacity post hoc.

## What V008 deliberately does not contain

No event/news features, no graph propagation, no causal macro vintages, no options, no analyst estimates/revisions, no fundamentals/valuation, no positioning/flow data, no broker costs and no path model.

That omission is part of the experiment. A broad V008 failure should trigger an information-acquisition branch, not a larger tree or neural network.

## Installation and scientific order

```bash
cd ~/quant_market_ai
unzip -o ~/Downloads/quant_market_ai_market_distributional_v008_conditional_residual_quantiles.zip -d .

python -m py_compile \
  models/market/distributional_v008_conditional_quantiles.py \
  evaluation/market/distributional_v008.py \
  pipeline/market_brain_distributional_v008.py \
  tools/patch_market_v008_docs_v001.py

python -m pytest tests/test_market_brain_distributional_v008.py -q

python tools/patch_market_v008_docs_v001.py --check
python tools/patch_market_v008_docs_v001.py --apply

python -m pipeline.market_brain_distributional_v008 --stage plan
```

Inspect `preregistration.json` and `resolved_feature_manifest.json`. The plan must PASS. Commit both source/docs and the resolved manifest before any benchmark.

Then run H1/H3/H5/H10 separately and finally summary.
