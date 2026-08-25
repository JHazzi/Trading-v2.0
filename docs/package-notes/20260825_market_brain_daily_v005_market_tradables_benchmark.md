# Market Brain Daily V005 — Market Tradables Benchmark V001

## Scientific question

Does adding SPY/QQQ/IWM state to the market-level component improve the frozen
V004 factorized reconstruction out of sample?

This is an information-ablation experiment, not a model search.

## Primary

```text
baseline:  V004 additive HGB reconstruction
candidate: V005 additive HGB reconstruction
```

Only the market component changes.

```text
V004 market features: 13
V005 external block:   22
candidate market total: 35
```

Sector and asset models are fitted once per fold and reused in both
reconstructions. The code also replays the V004 market model and hard-fails if
the reconstructed V004 predictions differ from the stored V004 OOS predictions
by more than 1e-9.

## Two separate gates

### Incremental information

```text
MAE(V004) - MAE(V005)
```

Positive means the external block improves V004.

Moving-block bootstrap uses 5/10/20 origin-day blocks, 3000 repetitions.

### Absolute skill

V005 is also compared to the original fold train-median baseline.

A robust V005>V004 result can justify retaining this information block even if
absolute skill versus train median has not yet been reached.

## Install

```bash
cd ~/quant_market_ai

unzip -o \
  ~/Downloads/quant_market_ai_market_daily_v005_market_tradables_benchmark.zip \
  -d .
```

## Compile/tests

```bash
python -m py_compile \
  evaluation/market/daily_v005_market_tradables_benchmark.py \
  models/market/daily_v005_market_tradables_benchmark.py \
  pipeline/market_brain_daily_v005_market_tradables_benchmark.py \
  tools/patch_market_v005_market_tradables_benchmark_docs_v001.py

python -m pytest \
  tests/test_market_brain_daily_v005_market_tradables_benchmark.py \
  -q
```

## Preregister docs

```bash
python tools/patch_market_v005_market_tradables_benchmark_docs_v001.py --check
python tools/patch_market_v005_market_tradables_benchmark_docs_v001.py --apply
```

Commit before model results.

## Plan first

```bash
python -m pipeline.market_brain_daily_v005_market_tradables_benchmark \
  --stage plan
```

Send:

```text
reports/market_brain_daily_v005/market_tradables_benchmark_v001/
benchmark_plan.json
```

Do not run horizons until the plan is checked.

## After a healthy plan

```bash
python -m pipeline.market_brain_daily_v005_market_tradables_benchmark \
  --stage run --horizon 1

python -m pipeline.market_brain_daily_v005_market_tradables_benchmark \
  --stage run --horizon 3

python -m pipeline.market_brain_daily_v005_market_tradables_benchmark \
  --stage run --horizon 5

python -m pipeline.market_brain_daily_v005_market_tradables_benchmark \
  --stage run --horizon 10

python -m pipeline.market_brain_daily_v005_market_tradables_benchmark \
  --stage summary
```

No features or hyperparameters may change between horizons.
