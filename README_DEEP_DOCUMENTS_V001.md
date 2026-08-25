# Deep SEC document scale v0.1

Metadata is already deep. This package downloads only filings that can actually
participate in the current Market/Event Brain dataset.

## Install

```bash
cd ~/quant_market_ai
unzip -o ~/Downloads/quant_market_ai_deep_documents_v001.zip -d .
```

## Compile

```bash
python -m py_compile \
  ingestion/events/sec_filing_documents_v2.py \
  pipeline/event_brain_deep_documents_v001.py
```

## Tests

```bash
python -m pytest \
  tests/test_deep_documents_v001_contract.py \
  tests/test_sec_documents_v2_contract.py \
  tests/test_sec_metadata_v3_contract.py \
  -q
```

## 1. Preflight first

```bash
python -m pipeline.event_brain_deep_documents_v001 \
  --stage preflight
```

Review:
- `totals.eligible_filings`
- `totals.already_downloaded_filings`
- `totals.pending_filings`
- each ticker's `price_ready_day`
- each ticker's pending count

Do not lower any gate just to proceed.

## 2. Optional one-batch smoke

The downloader itself was already proven in the 276-filing pilot, but for an
extra recovery checkpoint:

```bash
python -m pipeline.event_brain_deep_documents_v001 \
  --stage documents \
  --max-batches 1
```

Then:

```bash
python -m pipeline.event_brain_deep_documents_v001 \
  --stage audit
```

A rerun is idempotent at the content/storage layer and the pipeline reselects
only still-pending filings.

## 3. Full document scale

Use the existing `SEC_USER_AGENT`, then run:

```bash
python -m pipeline.event_brain_deep_documents_v001 \
  --stage documents
```

## 4. Final document audit

```bash
python -m pipeline.event_brain_deep_documents_v001 \
  --stage audit
```

The document stage is complete only when:

```text
documents_complete = true
totals.pending_filings = 0
```

## Recovery / per-ticker mode

To work on one issuer only:

```bash
python -m pipeline.event_brain_deep_documents_v001 \
  --stage documents \
  --ticker AAPL
```

You can also cap a recovery run:

```bash
python -m pipeline.event_brain_deep_documents_v001 \
  --stage documents \
  --ticker AAPL \
  --max-batches 5
```

## Next stage

Do not run mass clustering immediately after download.

The next package will:
1. derive deep clustering runs from the actual downloaded corpus;
2. keep normalized event identities stable across the pilot/deep rerun;
3. create a new deep Event State feature version so evidence counts are
   comparable across the entire rebuilt corpus;
4. rebuild 1/3/5/10-session labels;
5. run a science audit before retraining Event Brain v0.2 unchanged.
