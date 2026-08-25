# Market Brain Daily V004 — Factorization Foundation

## V003 result

The preregistered V003 Benchmark V001.1 is a useful negative result.

Primary pooled MAE delta (`train_median - HGB_full`):

```text
H1   -0.0484 pp
H3   -0.2945 pp
H5   -0.7561 pp
H10  -1.0025 pp
```

All 5/10/20-origin-day moving-block bootstrap intervals are strictly negative.

The most consistent degradation is:

```text
HGB own -> HGB own + cross-section
```

at every horizon.

Do not tune V003 after observing this result.

## Why V004 is factorized

V003 places three statistical levels into each asset-day row:

```text
own features          asset-day
cross-section state   mostly day-level
sector state          mostly sector-day
```

The next hypothesis is that these levels should not be learned as if they all
had the same effective sample size.

V004 therefore proposes:

```text
M_h(t)     = future equal-weight market return
S_h(s,t)   = future equal-weight sector return - M_h(t)
E_h(i,t)   = future asset return - future sector return

R_h(i,t) = M_h(t) + S_h(s(i),t) + E_h(i,t)
```

This factorization is algebraic. Whether it forecasts better is an empirical
question.

## Immediate gate

The included postmortem measures:

1. V003 model deltas and temporal bootstrap;
2. variance attributable to day-level common movement;
3. oracle market/sector residual MAE for diagnostic headroom;
4. how much true market-context features vary within a day;
5. how much true sector-context features vary within a sector-day;
6. whether `asset_minus_market` / `asset_minus_sector` retain asset-level
   within-unit variation, as they should.

The topology result tests the proposed explanation; it is not assumed true.

## Install

```bash
cd ~/quant_market_ai

unzip -o \
  ~/Downloads/quant_market_ai_market_daily_v004_factorization_foundation.zip \
  -d .
```

Compile/tests:

```bash
python -m py_compile \
  features/market/daily_v004_factorized_contract.py \
  evaluation/market/daily_v003_benchmark_postmortem.py \
  pipeline/market_brain_daily_v004.py \
  tools/patch_market_v003_results_v004_foundation_docs.py

python -m pytest \
  tests/test_market_brain_daily_v004_factorization_foundation.py \
  tests/test_market_brain_daily_v003_benchmark.py \
  tests/test_market_brain_daily_v003_benchmark_v0011.py \
  -q
```

Record the closed V003 result:

```bash
python tools/patch_market_v003_results_v004_foundation_docs.py --check
python tools/patch_market_v003_results_v004_foundation_docs.py --apply
```

Inspect V004 contract:

```bash
python -m pipeline.market_brain_daily_v004 --stage contract
```

Run the postmortem:

```bash
python -m pipeline.market_brain_daily_v004 --stage postmortem
```

Output:

```text
reports/market_brain_daily_v004/
factorization_foundation_postmortem.json
```

Send that JSON before materializing the three V004 component datasets.

## What would validate factorization as the next implementation

Evidence we expect to inspect, not assume:

- meaningful target variance is common at day level;
- sector-day oracle residuals remove additional variation;
- true market-context features have low within-day variance relative to total;
- true sector-context features have low within-sector-day variance;
- asset-relative features retain materially larger within-unit variance;
- V003 cross-context degradation is consistent across horizons/folds.

If those do not hold, V004 should be redesigned rather than implemented
because the architecture looked conceptually attractive.

## Deferred

Until this gate is read:

```text
no SPY / QQQ / IWM
no VIX / rates / credit
no macro
no Event Brain integration
no distributional training
no V003 hyperparameter search
```
