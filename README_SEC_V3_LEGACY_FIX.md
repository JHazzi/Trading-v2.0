# SEC v3 legacy-016 compatibility fix

## Root cause

The AAPL pilot already had metadata backfilled by migration 016.

Migration 016 enforces:

`UNIQUE(filing_raw_document_id, metadata_content_sha256)`

and metadata observations use a composite FK to:

`(metadata_version_id, filing_raw_document_id, normalized_raw_document_id)`.

The first `sec_edgar_v3` generated a different candidate version/raw pair for
identical content. `INSERT OR IGNORE` correctly kept the migrated version, but
the following observation still referenced the ignored candidate pair, causing:

`FOREIGN KEY constraint failed`

Only AAPL hit it because it was the only pilot asset with pre-016 SEC metadata.

## Fix

- Match migration 016's version id formula: accession + content SHA.
- After `INSERT OR IGNORE`, always resolve the canonical version/raw pair that
  actually exists.
- Use that pair in the metadata-observation composite FK.
- Keep all existing data. No cleanup or deletion is required.

## Validate

Copy the files over the repo, then:

```bash
python -m py_compile \
  ingestion/events/sec_metadata_logic.py \
  ingestion/events/sec_edgar_v3.py

python -m pytest \
  tests/test_sec_metadata_v3_contract.py \
  tests/test_event_brain_v001_contract.py \
  -q
```

## Re-run only SEC metadata

The previous run committed the other nine tickers successfully and rolled back
the failing AAPL submission transaction. Re-running is safe because versions
are immutable and retrievals/observations are append-only.

```bash
export SEC_USER_AGENT="QuantMarketAI/0.4 joaquinhazzi@gmail.com"

python -m pipeline.event_brain_scale_v001 \
  --stage sec-metadata

python -m pipeline.event_brain_scale_v001 \
  --stage audit
```

Expected:
- run status `completed`;
- errors `[]`;
- AAPL no longer raises a FK error;
- `sec_filings_with_metadata` should increase if AAPL contributes new selected
  filings;
- historical initial observations remain PIT=0 by design.
