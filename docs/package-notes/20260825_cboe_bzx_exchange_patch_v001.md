# CBOE BZX Exchange Support Patch V001

The Market Daily V003 discovery manifest found exactly one unresolved ticker:

```text
CBOE
```

Cboe Global Markets, Inc. is listed on **Cboe BZX Exchange**. The existing
daily-price contract only canonicalized NYSE and Nasdaq.

This patch adds a third canonical exchange/calendar identity:

```text
BATS
```

`exchange_calendars` uses `BATS` for the Cboe BZX U.S. equities calendar.

Accepted provider aliases:

```text
BATS
BZX
BTS
CBOE BZX
BZX EQUITIES
```

All canonicalize to:

```text
BATS
```

This is preferable to falsely labeling CBOE as NYSE or Nasdaq.

## Install

```bash
cd ~/quant_market_ai
unzip -o ~/Downloads/quant_market_ai_cboe_bzx_exchange_patch_v001.zip -d .
```

## Check/apply

```bash
python tools/patch_cboe_bzx_exchange_v001.py --check
python tools/patch_cboe_bzx_exchange_v001.py --apply
```

## Compile/tests

```bash
python -m py_compile \
  ingestion/prices/yahoo_daily_v1.py \
  tools/patch_cboe_bzx_exchange_v001.py

python -m pytest \
  tests/test_cboe_bzx_exchange_patch_v001.py \
  tests/test_daily_price_ingestion_contract.py \
  tests/test_market_brain_daily_v003_broad_backfill.py \
  -q
```

## Re-run discovery

The existing 492 READY rows are skipped automatically. The remaining CBOE
REVIEW row is retried:

```bash
python -m pipeline.market_brain_daily_backfill_v003 \
  --stage discover
```

Then:

```bash
python -m pipeline.market_brain_daily_backfill_v003 \
  --stage plan-audit \
  > reports/market_brain_daily_v003/broad_backfill_plan_audit.json
```

Expected:

```text
status = PASS
READY = 493
REVIEW = 0
ERROR = 0
discovery_complete = true
```
