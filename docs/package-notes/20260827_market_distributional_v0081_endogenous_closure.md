# Market Distributional V008.1 — Endogenous Closure

## Purpose

V008.1 is the final narrow falsification of the current endogenous daily
price/volume branch. It does not tune V008 after failure.

The frozen question is:

> Without recent post-model recalibration or cross-sectional/sector context,
> does the exact V008 own-state family improve H1 standardized-return quantiles
> beyond raw vol63?

The V008 H1 own-state diagnostic left a tiny positive point ambiguity versus
raw vol63 only after combining two already-observed comparisons. It did not
have a direct preregistered bootstrap, it used the harmful recent calibration
step, and its model was fit only on the development subset. V008.1 removes
those confounds and discloses that H1 was selected after V008.

## Frozen mathematics

For each purged expanding outer fold:

```text
m_train = median(outer_train return_pct)
z       = (return_pct - m_train) / asset_vol_63d_pct
```

on positive-scale rows.

Reference:

```text
Q_q(R | vol63)
= m_train + asset_vol_63d_pct_t * Q_train(z, q)
```

Candidate:

```text
Q_q(R | own_state)
= m_train + asset_vol_63d_pct_t * HGB_q(own_state_t)
```

The candidate uses the exact 14-feature V008 own-state manifest and the
shallow regularized profile selected in all 20 V008 fold/horizon selections.
There is no hyperparameter search and no post-model quantile or probability
calibration.

Rows with nonpositive vol63 retain the outer-train unconditional empirical
fallback.

## Capacity-matched placebo

Only H1 runs the expensive placebo battery. For seeds
`11,29,47,71,101`, the placebo:

- preserves aligned vol5/vol20/vol63;
- jointly deranges all other own-state features across assets within each
  origin day;
- preserves the day-level joint feature distribution;
- preserves targets, asset IDs and temporal clocks;
- uses the identical HGB profile and training weights.

A developmental pass requires the candidate to beat raw vol63 and the mean
placebo under the block-10 interval, beat every placebo seed by point estimate,
improve at least three quantiles, be positive in at least four folds and not
worsen mean absolute quantile calibration error.

## Claim boundary

H1 is primary because V008 generated the hypothesis. H3/H5/H10 are mandatory
diagnostics and cannot rescue H1.

The historical sample was already inspected in V008. Therefore even a complete
V008.1 gate pass is developmental only and requires a genuinely untouched
future temporal block before promotion. V008.1 cannot establish alpha,
profitability, strict-PIT validity, trajectory coherence or production
readiness.

## Fast validation already executed

```bash
cd ~/quant_market_ai

./.venv/bin/python -m py_compile \
  models/market/distributional_v0081_endogenous_closure.py \
  evaluation/market/distributional_v0081.py \
  pipeline/market_brain_distributional_v0081.py \
  tests/test_market_brain_distributional_v0081.py

./.venv/bin/python -m pytest \
  tests/test_market_brain_distributional_v0081.py -q
```

Expected result at package construction: `5 passed`.

## Scientific execution order

The following stages were deliberately not run while building the
infrastructure.

First freeze the plan and resolved manifest:

```bash
cd ~/quant_market_ai
./.venv/bin/python -m pipeline.market_brain_distributional_v0081 --stage plan
```

Require:

```text
reports/market_brain_distributional_v0081/endogenous_closure_v001/preregistration.json
status == PASS
```

Inspect and commit the source/config/docs plus
`resolved_feature_manifest.json` and `preregistration.json` before observing
V008.1 performance.

Run the expensive horizons separately:

```bash
./.venv/bin/python -m pipeline.market_brain_distributional_v0081 \
  --stage benchmark --horizons 1

./.venv/bin/python -m pipeline.market_brain_distributional_v0081 \
  --stage benchmark --horizons 3

./.venv/bin/python -m pipeline.market_brain_distributional_v0081 \
  --stage benchmark --horizons 5

./.venv/bin/python -m pipeline.market_brain_distributional_v0081 \
  --stage benchmark --horizons 10
```

H1 is expected to be the most computationally expensive because it trains five
capacity-placebo seeds in every outer fold.

After all four horizon reports exist:

```bash
./.venv/bin/python -m pipeline.market_brain_distributional_v0081 --stage summary
```

Return these artifacts for interpretation:

```text
reports/market_brain_distributional_v0081/endogenous_closure_v001/preregistration.json
reports/market_brain_distributional_v0081/endogenous_closure_v001/benchmark_summary.json
reports/market_brain_distributional_v0081/endogenous_closure_v001/h1_benchmark.json
reports/market_brain_distributional_v0081/endogenous_closure_v001/h3_benchmark.json
reports/market_brain_distributional_v0081/endogenous_closure_v001/h5_benchmark.json
reports/market_brain_distributional_v0081/endogenous_closure_v001/h10_benchmark.json
```

## Files introduced

```text
config/market_brain_distributional_v0081.json
models/market/distributional_v0081_endogenous_closure.py
evaluation/market/distributional_v0081.py
pipeline/market_brain_distributional_v0081.py
tests/test_market_brain_distributional_v0081.py
docs/package-notes/20260827_market_distributional_v0081_endogenous_closure.md
```
