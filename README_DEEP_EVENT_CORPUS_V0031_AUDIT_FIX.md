# V003.1 audit schema hotfix

The V003.1 normalization itself completed successfully.

The post-normalization audit crashed because it queried
`event_normalization_runs.filings_considered`, but migration 017 never
persisted that column. `filings_considered` is a runtime return metric from the
normalizer, not part of the DB contract.

This hotfix does NOT add a migration and does NOT rerun normalization.

Instead the audit reconstructs durable coverage from persisted lineage:

    event_evidence_semantics
      -> event_cluster_raw_membership_refs
      -> sec_filing_file_versions
      -> distinct filing_raw_document_id

It also checks that the persisted evidence-semantic row count matches the
`evidence_semantics_written` count recorded in each normalization run.

## Install

```bash
cd ~/quant_market_ai
unzip -o \
  ~/Downloads/quant_market_ai_deep_event_corpus_v0031_audit_fix.zip \
  -d .
```

## Compile + focused tests

```bash
python -m py_compile \
  evaluation/events/deep_corpus_audit_v003.py \
  pipeline/event_brain_deep_corpus_v003.py

python -m pytest \
  tests/test_deep_event_corpus_v0031_audit_schema.py \
  tests/test_deep_event_corpus_v0031_lineage.py \
  tests/test_deep_event_corpus_v003_form_guard.py \
  tests/test_deep_event_corpus_v003_contract.py \
  -q
```

## Do NOT rerun normalization

The ten V003.1 normalization runs are already persisted.

Run only:

```bash
python -m pipeline.event_brain_deep_corpus_v003 \
  --stage audit
```

Send that output before `states`.

Expected direction:
- normalization stage PASS;
- 10 completed normalization runs;
- persisted filing coverage approximately 1704/1704;
- persisted evidence semantics approximately 10642;
- unique event count around the actual normalized identity count;
- zero states/labels because those stages have intentionally not run.
