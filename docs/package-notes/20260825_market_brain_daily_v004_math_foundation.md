# Market Brain Daily V004 — Mathematical Foundation

## Why this package exists

V003 is preserved as a preregistered negative benchmark. The next step is not
to tune V003 or to add every available feature. The purpose of V004 is to
separate mathematical/statistical units before external enrichment.

Architecture Phase C remains active.

## Mathematical candidates

### Additive identity

```text
asset future return
= market future factor
+ sector future residual
+ asset future residual
```

This identity is exact by construction and is only a decomposition, not a
predictability claim.

### Dynamic factor identity

```text
R_i,h
= beta_i,t * M_h
+ gamma_i,t * S_sector,h
+ alpha_i,h
```

`beta_i,t` and `gamma_i,t` are rolling exposures estimated only with
observations available through the origin session close.

The primary long-window state uses 252 sessions, with 63-session estimates
stored as shorter-horizon diagnostics. No future outcome enters beta/gamma
features.

## Statistical units

```text
Market model:  origin day
Sector model:  origin day × sector
Asset model:   origin day × asset
```

This prevents treating a day-level market state repeated across hundreds of
asset rows as though it were hundreds of independent market observations.

## External information ladder

External data is deliberately staged after the mathematical dataset gate:

1. SPY / QQQ / IWM
2. sector ETFs
3. VIX + rates/credit market proxies
4. macro only after vintage/release/available_at contracts

Every stage must be an incremental walk-forward ablation.

## Install

```bash
cd ~/quant_market_ai

unzip -o \
  ~/Downloads/quant_market_ai_market_daily_v004_math_foundation.zip \
  -d .
```

## Compile and tests

```bash
python -m py_compile \
  features/market/daily_v004_math.py \
  evaluation/market/daily_v004_math_audit.py \
  pipeline/market_brain_daily_v004_math.py \
  tools/patch_market_v004_math_docs_v001.py

python -m pytest \
  tests/test_market_brain_daily_v004_math_foundation.py \
  -q
```

## Record research decision

```bash
python tools/patch_market_v004_math_docs_v001.py --check
python tools/patch_market_v004_math_docs_v001.py --apply
```

## Inspect contract

```bash
python -m pipeline.market_brain_daily_v004_math \
  --stage contract
```

## Build

```bash
python -m pipeline.market_brain_daily_v004_math \
  --stage build
```

Creates:

```text
data/processed/market_daily_v004_math.db
```

Tables:

```text
v004_market_states
v004_sector_states
v004_asset_states
v004_factor_targets
build_metadata
```

The V003 Core DB is read-only and remains unchanged.

## Audit

```bash
python -m pipeline.market_brain_daily_v004_math \
  --stage audit
```

Send:

```text
reports/market_brain_daily_v004/math_foundation_audit.json
```

The next package should only preregister/train the factorized benchmarks after
this audit confirms exact identities and sufficient dynamic-beta coverage.

## What this package does NOT claim

- factorization is not yet proven predictive;
- V003 is not discarded;
- external proxies are not yet ingested;
- no event/news feature is used;
- no distributional model is trained;
- no trading claim is made.


## Mathematical ladder after this dataset audit

The additive/equal-weight factor is a baseline, not a dogma.

If the factorized dataset is healthy, the benchmark sequence should compare:

```text
1. equal-weight additive factorization
2. dynamic beta/gamma factorization
3. rolling train-only PCA/statistical factors as robustness
4. shrinkage/state-space exposures only if dynamic beta is noisy
```

Target views must also be distinguished:

```text
absolute return
market-relative return
sector-relative return
cross-sectional rank
```

A method is only retained if it adds paired walk-forward value. Full-sample
PCA is prohibited because its loadings would use future information.

Regime models (observable state buckets, change-point models, hidden-state
models) are candidates after the factorization gate, not an excuse to tune
V003 retrospectively.
