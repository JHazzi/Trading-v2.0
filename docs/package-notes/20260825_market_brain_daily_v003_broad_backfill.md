# Market Brain Daily V003 — Broad Equity Backfill

**Date:** 2026-08-25  
**Purpose:** populate the all-asset-day Market V003 research panel before any new training.

## Foundation result that triggered this package

```text
active equities                       503
quality-gated daily assets             10
assets >= 1,260 sessions               10
assets >= 2,000 sessions               10
latest 253-day-ready assets            10
minimum 50-asset cross-section day   none
strict historical PIT rows              0
```

Decision:

```text
BROAD_PANEL_BACKFILL_REQUIRED
```

## Scientific scope

This is a **current-cohort historical research panel**, not a historically
constituent-complete S&P 500 panel.

Do not use results from this cohort as a survivorship-free index backtest.

The broad backfill intentionally excludes:

```text
SPY / QQQ / IWM
sector ETFs
VIX
rate / credit ETFs
macro
events
```

Those are deferred. The first Market V003 benchmark should tell us whether
own-asset + broad cross-section + sector context already improves the base
forecast before adding external context.

## Why discovery exists before ingestion

The current 503 assets have no persisted exchange metadata and many companies
did not trade for the full 2016–2026 window.

The existing daily quality contract correctly treats missing expected exchange
sessions as failures. Therefore requesting 2016 for a 2020 IPO would
incorrectly fail quality.

Discovery performs a non-persisted Yahoo query only to learn:

```text
provider symbol
first available daily row
last available daily row
exchange metadata
```

Then the real ingestion re-fetches exactly:

```text
[first_available_day, 2026-08-25)
```

through the existing `yahoo_daily_v1` causal append-only path.

The discovery response is not represented as raw market evidence.

## Install

```bash
cd ~/quant_market_ai

unzip -o \
  ~/Downloads/quant_market_ai_market_daily_v003_broad_backfill.zip \
  -d .
```

## Compile and test

```bash
python -m py_compile \
  ingestion/prices/yahoo_daily_broad_v003.py \
  evaluation/market/daily_v003_backfill_audit.py \
  pipeline/market_brain_daily_backfill_v003.py \
  tools/patch_market_v003_backfill_docs_v001.py

python -m pytest \
  tests/test_market_brain_daily_v003_broad_backfill.py \
  tests/test_daily_price_ingestion_contract.py \
  tests/test_market_brain_daily_v003_foundation.py \
  -q
```

## Record the foundation decision

```bash
python tools/patch_market_v003_backfill_docs_v001.py --check
python tools/patch_market_v003_backfill_docs_v001.py --apply
```

## Step 1 — Preflight

```bash
python -m pipeline.market_brain_daily_backfill_v003 \
  --stage preflight
```

Expected approximately:

```text
active_equities = 503
existing_ready_assets = 10
pending_assets = 493
assets_missing_exchange_metadata = 503
```

## Step 2 — Discovery

This uses network access but does not write price observations.

```bash
python -m pipeline.market_brain_daily_backfill_v003 \
  --stage discover
```

It is resumable. Detailed manifest:

```text
reports/market_brain_daily_v003/broad_backfill_manifest.json
```

If interrupted, run the same command again.

If provider errors occurred and you deliberately want to retry:

```bash
python -m pipeline.market_brain_daily_backfill_v003 \
  --stage discover \
  --retry-errors
```

## Step 3 — Plan audit

```bash
python -m pipeline.market_brain_daily_backfill_v003 \
  --stage plan-audit
```

Do not ingest the full cohort unless this is `PASS`.

If a small number of tickers remain `REVIEW`, inspect the manifest rather than
guessing an exchange. `config/market_brain_daily_v003_backfill.json` supports
explicit `exchange_overrides` and `provider_symbol_overrides`; any override is
part of the frozen config hash.

If the config changes after discovery, deliberately archive/remove the old
manifest and re-run discovery. The pipeline refuses silent plan drift.

## Step 4 — Five-asset smoke ingestion

```bash
python -m pipeline.market_brain_daily_backfill_v003 \
  --stage smoke
```

This is a real DB write through `yahoo_daily_v1`.

Then audit:

```bash
python -m pipeline.market_brain_daily_backfill_v003 \
  --stage audit
```

A five-asset smoke will normally remain `REVIEW` because the 300-asset panel
target has not yet been reached. What matters is:

```text
no failed smoke tickers
new assets appear in quality-gated daily coverage
corporate actions expand
```

## Step 5 — Full resumable backfill

Only after a healthy smoke:

```bash
python -m pipeline.market_brain_daily_backfill_v003 \
  --stage backfill
```

The checkpoint is written after every asset:

```text
reports/market_brain_daily_v003/broad_backfill_checkpoint.json
```

If interrupted, run the same command again. Completed assets are skipped.

Do not use `--retry-failed` automatically. First inspect why a ticker failed.

## Step 6 — Final audit

```bash
python -m pipeline.market_brain_daily_backfill_v003 \
  --stage audit
```

Detailed result:

```text
reports/market_brain_daily_v003/broad_backfill_audit.json
```

Primary research readiness gates:

```text
>= 300 assets ready for 253-day states
>= 300 assets with >= 1,260 daily sessions
```

These are minimum gates, not targets to cherry-pick a subset.

## After PASS

Do **not** add proxies yet.

The next package should build:

```text
Market Daily V003 core dataset
  = own asset state
  + leave-one-out market cross-section
  + leave-one-out sector context
  + raw-close targets with corporate-action overlap exclusions
```

Then compare simple and nonlinear models against naive baselines.

External market proxies become an incremental experiment after the core
all-asset-day Market Brain has a reproducible benchmark.
