# Market Brain Daily V003 — Benchmark V001

## Why the Core gate is closed

Corrected Core audit:

```text
PASS
1,092,555 states
497 assets
cross-section peers: min 462 / median 485
sector missing: 0
```

Usable labels:

```text
H1  1,078,329
H3  1,049,926
H5  1,021,619
H10   951,231
```

The benchmark is now frozen before seeing any model performance.

## Scientific question

Does the market state `X_t` improve out-of-sample forecasts over honest
train-only naive baselines?

Primary:

```text
train median
vs
HistGradientBoosting full state
```

The primary model is not selected after results.

## Decomposition

Nonlinear models intentionally test:

```text
own only
own + whole-market cross-section
own + cross-section + sector
```

This identifies whether context adds incremental value rather than treating
all features as one opaque bundle.

## Baselines

```text
zero
train mean
train median
asset-specific train mean
same-horizon historical momentum
```

All mean/median baselines use training rows only.

## Linear/robust models

```text
Ridge(alpha=1)
SGDRegressor(loss=huber)
```

SGD Huber is used instead of sklearn HuberRegressor because the latter is not
appropriate for repeated million-row fits.

## Nonlinear model

HistGradientBoosting is the first controlled nonlinear model.

It is used instead of RandomForest in the primary benchmark because V003 has
roughly one million rows per horizon and five walk-forward fits per horizon.
Random Forest is a later robustness experiment if the signal merits one.

No tree hyperparameter search is performed.

## Temporal evaluation

Five expanding temporal folds.

```text
initial_fraction = 0.30
```

For each fold:

```text
target_trading_day < first_test_origin_day
```

for every training row.

This purges overlapping future targets at the train/test boundary.

The earlier OOS start intentionally exposes 2020-era regimes rather than
letting 2017–2020 exist only as training context.

## Dependence-aware inference

Do not use row-level iid intervals across hundreds of same-day assets.

Primary paired inference aggregates loss by origin day and runs moving-block
bootstrap with:

```text
5
10
20 origin days
```

3000 repetitions.

## Cross-sectional diagnostics

Each model also reports daily:

```text
Pearson IC
Spearman rank IC
market-direction accuracy of daily mean forecast
```

so a model cannot hide behind a small aggregate MAE improvement while having
no cross-sectional information.

## Run order

Install:

```bash
cd ~/quant_market_ai
unzip -o \
  ~/Downloads/quant_market_ai_market_daily_v003_benchmark.zip \
  -d .
```

Compile/tests:

```bash
python -m py_compile \
  evaluation/market/daily_v003_benchmark.py \
  models/market/daily_v003_benchmark.py \
  pipeline/market_brain_daily_benchmark_v003.py \
  tools/patch_market_v003_benchmark_docs_v001.py

python -m pytest \
  tests/test_market_brain_daily_v003_benchmark.py \
  tests/test_market_brain_daily_v003_core_dataset.py \
  tests/test_market_brain_daily_v003_core_h1_fix.py \
  -q
```

Record preregistration:

```bash
python tools/patch_market_v003_benchmark_docs_v001.py --check
python tools/patch_market_v003_benchmark_docs_v001.py --apply
```

Plan first:

```bash
python -m pipeline.market_brain_daily_benchmark_v003 \
  --stage plan
```

Inspect/send:

```text
reports/market_brain_daily_v003/benchmark_v001/benchmark_plan.json
```

Do not run models if the plan does not match the frozen five-fold contract.

Then run horizons independently:

```bash
python -m pipeline.market_brain_daily_benchmark_v003 --stage run --horizon 1
python -m pipeline.market_brain_daily_benchmark_v003 --stage run --horizon 3
python -m pipeline.market_brain_daily_benchmark_v003 --stage run --horizon 5
python -m pipeline.market_brain_daily_benchmark_v003 --stage run --horizon 10
```

Each horizon writes a compact JSON report and a gzip-compressed OOS prediction
file.

After all four:

```bash
python -m pipeline.market_brain_daily_benchmark_v003 --stage summary
```

Do not add proxies or tune models between horizons.
