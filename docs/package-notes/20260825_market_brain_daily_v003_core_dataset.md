# Market Brain Daily V003 — Core Dataset

Broad acquisition has crossed the scientific readiness gate:

```text
490 / 493 backfills completed
3 quality-quarantined: FISV, HUBB, MNST
500 quality-gated daily assets
497 assets >=253 sessions
489 assets >=1260 sessions
```

The three quarantined retrievals must not be rescued by weakening quality
rules. They can be repaired separately later.

## Purpose

Materialize the first Market Brain dataset independent of event occurrence:

```text
all eligible asset-days
  + own returns/vol/range/volume/drawdown
  + leave-one-out whole-market context
  + leave-one-out sector context
  -> separate future labels 1/3/5/10 sessions
```

No SEC/news/event features. No SPY/QQQ/IWM/VIX/rates/macro yet.

## Storage

Creates a rebuildable processed SQLite file only:

```text
data/processed/market_daily_v003_core.db
```

It does not mutate `market_data_v2.db`.

States use only quality-gated rows with `available_at <= session close` under
`historical_session_close_assumption`; therefore historical PIT remains 0.

Labels store terminal return, MFE, MAE and realized path volatility. Horizons
containing a currently-present corporate action are marked
`corporate_action_overlap` rather than used as primary raw-close labels.

## Install

```bash
cd ~/quant_market_ai
unzip -o ~/Downloads/quant_market_ai_market_daily_v003_core_dataset.zip -d .
```

## Compile/tests

```bash
python -m py_compile \
  features/market/daily_v003_core.py \
  evaluation/market/daily_v003_core_audit.py \
  pipeline/market_brain_daily_core_v003.py \
  tools/patch_market_v003_core_docs_v001.py

python -m pytest \
  tests/test_market_brain_daily_v003_core_dataset.py \
  tests/test_market_brain_daily_v003_foundation.py \
  tests/test_market_brain_daily_v003_broad_backfill.py \
  -q
```

## Record decision

```bash
python tools/patch_market_v003_core_docs_v001.py --check
python tools/patch_market_v003_core_docs_v001.py --apply
```

## Check quarantine and contract

```bash
python -m pipeline.market_brain_daily_core_v003 --stage quarantine
python -m pipeline.market_brain_daily_core_v003 --stage contract
```

Expected quarantine: `FISV`, `HUBB`, `MNST`.

## Build

```bash
python -m pipeline.market_brain_daily_core_v003 --stage build
```

The panel is roughly one million asset-days, so this stage is intentionally
batch-like and can use substantial RAM/CPU.

## Audit

```bash
python -m pipeline.market_brain_daily_core_v003 --stage audit
```

Attach:

```text
reports/market_brain_daily_v003/core_dataset_audit.json
```

Before training we specifically inspect state count/coverage, sector missing
rate, all four label-status distributions, and especially H10 corporate-action
exclusion. If that exclusion is large, we design a total-return label rather
than silently accepting selection bias.
