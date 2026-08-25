# Deep History Metadata v0.1 FIX

This fixes the AAPL-only failure from the first deep-history run.

## Root cause

The history v4 writer was accidentally based on the pre-fix v3 writer. AAPL
already had migration-016 legacy metadata, so identical content could win an
`INSERT OR IGNORE` under an older immutable metadata version/raw pair. The new
observation then referenced the candidate pair and failed the composite FK.

The fix restores `canonical_metadata_version_reference(...)`, which resolves the
version/raw pair that actually exists before writing the observation.

The nine successful issuers do not need to be rerun.

## Audit cleanup

The audit now counts only the configured deep-history forms:
8-K, 8-K/A, 10-Q, 10-Q/A, 10-K, 10-K/A.

Existing unrelated Form 4 metadata for AAPL will no longer be included.

## Install

```bash
cd ~/quant_market_ai
unzip -o ~/Downloads/quant_market_ai_deep_history_metadata_v001_fix.zip -d .
```

## Compile + test

```bash
python -m py_compile \
  ingestion/events/sec_metadata_logic.py \
  ingestion/events/sec_edgar_v4_history.py \
  pipeline/event_brain_deep_history_v001.py

python -m pytest \
  tests/test_sec_edgar_v4_history_contract.py \
  tests/test_sec_metadata_v3_contract.py \
  -q
```

## Recover AAPL only

```bash
python -m pipeline.event_brain_deep_history_v001 \
  --stage repair-aapl
```

Then:

```bash
python -m pipeline.event_brain_deep_history_v001 \
  --stage audit
```

Do not download the deep historical filing documents yet.

After the audit is green, the next stage will restrict expensive document/event
processing to the interval supported by the daily-price history (2016 onward),
deduplicate against the existing pilot lineage, rebuild labels, and rerun the
same Event Brain v0.2 benchmark unchanged.
