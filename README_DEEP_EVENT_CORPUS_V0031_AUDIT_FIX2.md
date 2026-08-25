# V003.1 audit fix 2 — semantic count multiplicity

The corpus itself does not need to be rebuilt.

The previous audit counted `persisted_evidence_semantics` after joining semantic
rows to `sec_filing_file_versions`. If one immutable raw document is referenced
by more than one file-version lineage row, that JOIN can multiply one semantic
row.

The database contract already guarantees:

    UNIQUE(normalization_run_id, membership_id)

for `event_evidence_semantics`.

Therefore the durable semantic count must be read directly from
`event_evidence_semantics`. Filing coverage remains a separate lineage-derived
metric.

## Install

```bash
cd ~/quant_market_ai
unzip -o \
  ~/Downloads/quant_market_ai_deep_event_corpus_v0031_audit_fix2.zip \
  -d .
```

## Validate

```bash
python -m py_compile \
  evaluation/events/deep_corpus_audit_v003.py \
  pipeline/event_brain_deep_corpus_v003.py

python -m pytest \
  tests/test_deep_event_corpus_v0031_audit_join_fix.py \
  tests/test_deep_event_corpus_v0031_audit_schema.py \
  tests/test_deep_event_corpus_v0031_lineage.py \
  tests/test_deep_event_corpus_v003_form_guard.py \
  tests/test_deep_event_corpus_v003_contract.py \
  -q
```

## Do not rerun normalization

Run only:

```bash
python -m pipeline.event_brain_deep_corpus_v003 \
  --stage audit
```

If normalization becomes PASS with:
- 1704 persisted filings;
- 10642 persisted evidence semantics;
- 1939 unique events;

then the SEC deep-normalization stage is closed and the next stage is
Event State V003.1, followed by labels and the science audit.
