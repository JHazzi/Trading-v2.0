# Market Brain Daily V005.2 — Financial Conditions

## Why this stage exists

V004 materially repaired the pooled V003 architecture but did not pass the
absolute-return baseline. V005.1 then tested SPY/QQQ/IWM as an incremental
market-information block and did not obtain robust incremental skill.

V005.2 changes the information hypothesis rather than tuning a failed model:

```text
V004 frozen control
+ volatility
+ rates/duration
+ credit
```

## Repository alignment

Tradable references reuse the existing causal Yahoo daily price ingestion
layer rather than creating another lightweight price store. The existing layer
already owns raw content-addressed batches, versioned observations,
`available_at`, revision history, and quality results.

Reference ETFs are inserted as `asset_type=etf_reference`, `active=0`, so new
rows do not become constituents of the prediction universe.

HYG/LQD are NYSE Arca listed. An idempotent patch adds canonical `ARCX` aliases
to the Yahoo ingestor and uses XNYS as its U.S. equity session-calendar proxy.

## VIX clock

The equity prediction origin is the U.S. regular-session close. Cboe VIX RTH
continues to approximately 16:15 ET, later than the equity-origin clock.
Therefore V005.2 never uses the same-day official daily VIX close.

```text
VIX feature clock = previous VIX session
```

All VIX features are explicitly named `vix_lag1_*`.

## ETF return convention

Neither naive unadjusted price returns nor retrospective Yahoo Adj Close are
used directly.

For each ETF day:

```text
r_t = (Close_t + cash_distribution_t) / Close_{t-1} - 1
```

where `cash_distribution_t` contains only dividend/capital-gain actions whose
effective trading day is `t`. Multi-session returns compound these daily
returns.

This prevents a bond ETF ex-distribution drop from being mistaken for rate or
credit stress without importing future adjustment factors from a current
Adj-Close history. Historical action reconstruction remains `strict PIT=false`
and is explicitly audited.

## External state

### VIX — 6 pure + 1 interaction

- lagged VIX level
- 1d / 5d point changes
- 63d / 252d z-scores
- 20d volatility of lagged log changes
- VIX minus annualized 20d realized market volatility

### Rates — 7

- TLT return 1d / 5d / 20d
- IEF minus SHY 5d
- TLT minus IEF 5d
- TLT minus SHY 20d
- TLT realized volatility 20d

### Credit — 6

- HYG return 1d / 5d
- HYG minus LQD 1d / 5d / 20d
- HYG realized volatility 20d

Primary external block: **20 features**.
V004 base market state: **13 features**.
Primary candidate market state: **33 features**.

## Benchmark contract

Primary:

```text
V004 additive HGB reconstruction
vs
V004 + full V005.2 financial-conditions block
```

Only the market model changes. Sector model, asset model, targets, folds,
hyperparameters and OOS state rows remain frozen.

Secondary preregistered diagnostics:

```text
VIX-only
rates-only
credit-only
```

Each receives a paired moving-block bootstrap, but none can rescue a failed
full-block primary after results.

## Install

```bash
cd ~/quant_market_ai
unzip -o \
  ~/Downloads/quant_market_ai_market_daily_v0052_financial_conditions.zip \
  -d .
```

## 1. Inspect/apply NYSE Arca support

```bash
python tools/patch_yahoo_daily_arca_support_v001.py --check
python tools/patch_yahoo_daily_arca_support_v001.py --apply
git diff -- ingestion/prices/yahoo_daily_v1.py
```

Expected conceptual addition:

```text
ARCX / ARCA / NYSE ARCA / NYSEARCA / PCX -> ARCX
ARCX session-calendar proxy -> XNYS
```

If `--check` prints `unexpected_source`, do not force the patch; inspect the
local Yahoo ingestor because it has diverged from the reviewed public version.

## 2. Tests

```bash
python -m pytest \
  tests/test_market_brain_daily_v0052_financial_conditions.py \
  -q
```

## 3. Record research decision

```bash
python tools/patch_market_v0051_results_v0052_docs.py --check
python tools/patch_market_v0051_results_v0052_docs.py --apply
```

Commit before acquiring/modeling results if you want the strongest
preregistration trail.

## 4. Seed inactive reference assets

```bash
python -m pipeline.market_brain_daily_v0052_financial_conditions \
  --stage seed-assets
```

## 5. Acquire rates/credit ETFs through the canonical Yahoo layer

```bash
python -m pipeline.market_brain_daily_v0052_financial_conditions \
  --stage acquire-etfs
```

## 6. Acquire official Cboe VIX daily history

```bash
python -m pipeline.market_brain_daily_v0052_financial_conditions \
  --stage acquire-vix
```

## 7. Build state

```bash
python -m pipeline.market_brain_daily_v0052_financial_conditions \
  --stage build
```

## 8. Audit — stop here

```bash
python -m pipeline.market_brain_daily_v0052_financial_conditions \
  --stage audit
```

Send:

```text
reports/market_brain_daily_v0052/
financial_conditions_foundation_audit.json
```

Do **not** run the benchmark before the audit is reviewed.

## 9. After a healthy audit

```bash
python -m pipeline.market_brain_daily_v0052_financial_conditions \
  --stage benchmark-plan
```

Send:

```text
reports/market_brain_daily_v0052/
financial_conditions_benchmark_v001/benchmark_plan.json
```

## 10. Only after a healthy plan

```bash
python -m pipeline.market_brain_daily_v0052_financial_conditions \
  --stage benchmark-run --horizon 1
python -m pipeline.market_brain_daily_v0052_financial_conditions \
  --stage benchmark-run --horizon 3
python -m pipeline.market_brain_daily_v0052_financial_conditions \
  --stage benchmark-run --horizon 5
python -m pipeline.market_brain_daily_v0052_financial_conditions \
  --stage benchmark-run --horizon 10
python -m pipeline.market_brain_daily_v0052_financial_conditions \
  --stage benchmark-summary
```

No feature, parameter or primary interpretation may change between horizons.
