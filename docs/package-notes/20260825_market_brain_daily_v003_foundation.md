# Market Brain Daily V003 — Foundation Audit

**Date:** 2026-08-25  
**Stage:** foundation / data-contract audit  
**Writes to DB:** no

## Scientific change

Market Brain Daily V003 is defined as an independent base model trained on
**all eligible asset-days**, not only days on which SEC events occurred.

State clock:

```text
exchange session close
```

Future Event Brain integration:

```text
market_state_time <= event_state_time
```

An intraday event therefore cannot use that day's future close.

## Event t0 decision

This package also includes an idempotent documentation patch recording:

```text
source authority != first public disclosure
SEC accepted_at != universal event t0
```

Future Event Brain work will distinguish `event_time`, `first_public_at`,
source publication/acceptance, system observation and feature `available_at`.

SEC remains the authoritative anchor corpus, but not a universal fastest feed.

## Why an audit before backfill

The DB already contains:

- a 503-ish asset universe from prior migration;
- legacy intraday data;
- causal daily price observation/version tables;
- a deep daily research history for the 10 Event Brain assets.

We do not yet assume that hundreds of assets have 5–10 years of quality-gated
daily history.

Before downloading a broad panel, V003 measures exactly what exists.

## Planned initial Market V003 state

Own-asset features:

```text
returns 1/3/5/10/20/63
volatility 5/20/63
range
volume ratio
drawdown 20/63/252
```

Cross-section features are leave-one-out and use only eligible assets whose
daily state was available by the session close.

Sector features are also leave-one-out.

The initial research cohort is explicitly a **current-asset historical
research cohort**, not a survivorship-free historical index universe.

Adjusted close remains audit-only for identity/feature semantics. Initial
targets use raw closes and conservatively exclude corporate-action overlaps.

Macro is disabled until publication/vintage/availability semantics exist.

## Install

```bash
cd ~/quant_market_ai

unzip -o \
  ~/Downloads/quant_market_ai_market_daily_v003_foundation.zip \
  -d .
```

## Compile/tests

```bash
python -m py_compile \
  features/market/daily_v003_contract.py \
  evaluation/market/daily_v003_foundation_audit.py \
  pipeline/market_brain_daily_v003.py \
  tools/patch_event_t0_docs_v001.py

python -m pytest \
  tests/test_market_brain_daily_v003_foundation.py \
  tests/test_event_t0_docs_patch_v001.py \
  tests/test_daily_price_ingestion_contract.py \
  tests/test_daily_price_projection.py \
  -q
```

## Apply documentation decision

Preview:

```bash
python tools/patch_event_t0_docs_v001.py --check
```

Apply once:

```bash
python tools/patch_event_t0_docs_v001.py --apply
```

Running `--apply` again is safe; it reports `already_applied`.

## Inspect the V003 contract

```bash
python -m pipeline.market_brain_daily_v003 \
  --stage contract
```

## Run the foundation audit

```bash
python -m pipeline.market_brain_daily_v003 \
  --stage audit
```

It also writes:

```text
reports/market_brain_daily_v003/foundation_audit.json
```

Send that JSON/output before performing a broad daily-price backfill.

## What the audit decides

Important fields:

```text
assets.active_equities
daily_quality_gated.assets_with_daily_data
daily_quality_gated.assets_by_minimum_history_days
dynamic_panel_readiness
sector_readiness_latest
proxy_coverage
macro.causal_vintage_contract
recommendation.price_panel_status
```

Possible panel decisions:

```text
BROAD_PANEL_READY
PARTIAL_PANEL_READY
BROAD_PANEL_BACKFILL_REQUIRED
```

Do not start training V003 before this gate.
