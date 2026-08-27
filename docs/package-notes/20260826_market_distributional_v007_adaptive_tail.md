# Market Brain Distributional V007 — Adaptive Asymmetric Asset Scale

**Status:** preregistration package. Do not interpret V007 performance before committing the specification.

## Why V007 exists

V006.1 reproduced V006 exactly and found that the positive conditional-dispersion result is not driven by one asset or sector. At the same time it narrowed the model form:

- vol5 is consistently worse than vol20;
- vol63 is stronger than vol20 at H3/H5/H10;
- V006 gains are concentrated much more in q75/q95 than in q05/q25;
- q05 becomes negative at H5 and significantly negative at H10;
- low-vol regimes are under-covered while high-vol regimes are over-covered;
- the asset empirical distribution remains competitive, especially at longer horizons.

The next experiment therefore should not be a bigger generic model. It should first test the smallest mathematical generalization that directly addresses those failures.

## Frozen model

V007 does **not** learn direction. The median is always the global training median.

For each asset, the training distribution provides structural tail widths:

```text
w_i,q = Q_i,q(train) - Q_i,50(train)
```

The dynamic volatility state is normalized relative to the same asset's training history:

```text
r20 = vol20_i,t / median_train_i(vol20)
r63 = vol63_i,t / median_train_i(vol63)

u = lambda20 * log(r20) + (1-lambda20) * log(r63)
```

Each side has its own multiplier:

```text
g_side = kappa_side * exp(alpha_side * u)
```

and quantiles are:

```text
Q_q(i,t) = global_train_median + w_i,q * g_down   for q05/q25
Q_50(i,t) = global_train_median
Q_q(i,t) = global_train_median + w_i,q * g_up     for q75/q95
```

This permits:

- persistent asset heterogeneity;
- longer/shorter volatility memory through `lambda20`;
- sublinear or superlinear regime response through `alpha`;
- separate downside and upside dynamics;
- no quantile crossing by construction;
- no location-alpha claim.

## Nested temporal selection

Inside each outer V003 training period, the most recent 20% of origin days becomes an inner validation set. Inner training is purged so every training target ends before the first validation origin day.

Downside parameters are selected by q05/q25 origin-day-equal pinball. Upside parameters are selected by q75/q95 origin-day-equal pinball.

Frozen grids:

```text
alpha    = 0.00, 0.25, 0.50, 0.75, 1.00, 1.25
lambda20 = 0.00, 0.25, 0.50, 0.75, 1.00
kappa    = 0.80, 1.00, 1.20
```

The outer test is never used for parameter selection.

## Controls

Primary reference:

```text
vol63_scaled_empirical
```

This is a **new prospective V007 reference** justified by the already-completed V006.1 sensitivity. It does not rewrite V006.

Secondary references:

```text
vol20_scaled_empirical   # exact V006 form
asset_empirical
global train empirical
```

## Interpretation

Per horizon:

- `PASS_STRONG`: block-10 95% CI for candidate-vs-vol63 pinball delta is entirely positive **and** mean absolute quantile calibration error is no worse than vol63.
- `PASS_SCORE_ONLY_CALIBRATION_WORSE`: proper score improves significantly but calibration worsens.
- `INCONCLUSIVE_POSITIVE_POINT`: point estimate improves but CI crosses zero.
- `FAIL`: non-positive point estimate or CI entirely negative.

A multi-horizon candidate requires at least three `PASS_STRONG` horizons and no `FAIL` horizon.

Even then, V007 remains developmental because V006.1 outcomes informed this mathematical hypothesis. Independent/prospective confirmation is a later gate.

## Install

From the repository root:

```bash
cd ~/quant_market_ai
unzip -o ~/Downloads/quant_market_ai_market_distributional_v007_adaptive_tail.zip -d .
```

Compile:

```bash
python -m py_compile \
  models/market/distributional_v007_adaptive_tail.py \
  evaluation/market/distributional_v007.py \
  pipeline/market_brain_distributional_v007.py \
  tools/patch_market_v007_docs_v001.py
```

Tests:

```bash
python -m pytest tests/test_market_brain_distributional_v007.py -q
```

## Update canonical docs before performance

```bash
python tools/patch_market_v007_docs_v001.py --check
python tools/patch_market_v007_docs_v001.py --apply
```

## Plan gate

```bash
python -m pipeline.market_brain_distributional_v007 --stage plan
```

Require `status: PASS`.

Then commit the specification **before** benchmarking:

```bash
git status --short
git add -A
git commit -m "research: preregister Market Distributional V007 adaptive tails"
git push
```

## Benchmark

Run one horizon at a time if desired:

```bash
python -m pipeline.market_brain_distributional_v007 --stage benchmark --horizons 1
python -m pipeline.market_brain_distributional_v007 --stage benchmark --horizons 3
python -m pipeline.market_brain_distributional_v007 --stage benchmark --horizons 5
python -m pipeline.market_brain_distributional_v007 --stage benchmark --horizons 10
```

Then:

```bash
python -m pipeline.market_brain_distributional_v007 --stage summary
```

Return these five JSON files for interpretation:

```text
reports/market_brain_distributional_v007/adaptive_tail_v001/h1_benchmark.json
reports/market_brain_distributional_v007/adaptive_tail_v001/h3_benchmark.json
reports/market_brain_distributional_v007/adaptive_tail_v001/h5_benchmark.json
reports/market_brain_distributional_v007/adaptive_tail_v001/h10_benchmark.json
reports/market_brain_distributional_v007/adaptive_tail_v001/benchmark_summary.json
```

Do not tune the grids, change the primary reference, add features or select a best horizon after seeing V007 results.
