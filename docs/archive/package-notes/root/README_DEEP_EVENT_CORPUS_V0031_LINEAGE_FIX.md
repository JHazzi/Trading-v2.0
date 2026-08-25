# Deep Event Corpus V003.1 — lineage fix

Do not run states/labels from the bad V003 normalization.

The old V003 normalization rows remain in the DB as immutable audit history.
V003.1 uses new version identifiers, so no delete/rollback is required.

## Install

```bash
cd ~/quant_market_ai
unzip -o \
  ~/Downloads/quant_market_ai_deep_event_corpus_v0031_lineage_fix.zip \
  -d .
```

## Compile

```bash
python -m py_compile \
  ingestion/events/sec_event_normalizer_v003_deep.py \
  features/events/event_state_v003_deep.py \
  evaluation/targets/event_reaction_targets_v003_deep.py \
  evaluation/events/deep_corpus_audit_v003.py \
  pipeline/event_brain_deep_corpus_v003.py
```

## Tests

```bash
python -m pytest \
  tests/test_deep_event_corpus_v0031_lineage.py \
  tests/test_deep_event_corpus_v003_form_guard.py \
  tests/test_deep_event_corpus_v003_contract.py \
  tests/test_event_temporal_normalization_v002.py \
  -q
```

## 1. Read-only lineage audit

No clustering rerun is needed.

```bash
python -m pipeline.event_brain_deep_corpus_v003 \
  --stage lineage-audit
```

Required:
- `lineage.status = PASS`;
- raw membership refs ~= raw memberships;
- mapped memberships ~= raw memberships;
- zero ambiguous lineage;
- zero strict-PIT memberships missing temporal refs.

It is EXPECTED that many historical documents have actual retrieval timestamps
after the historical cutoff, because they were downloaded during the 2026
backfill. Those rows must remain PIT=0.

## 2. Corrected normalization

Only if lineage audit passes:

```bash
python -m pipeline.event_brain_deep_corpus_v003 \
  --stage normalize
```

V003.1 creates new normalization runs:
`sec_event_normalizer_v0031_deep_raw_lineage`.

The two bad V003 AAPL observations and nine empty V003 runs are not reused.

## 3. Stop and review

Do NOT run states or labels yet.

Send:
- the complete `lineage-audit`;
- the normalization summary/audit.

Important fields:
- `stage_status.normalization`;
- `normalization_runs`;
- `normalized.unique_events`;
- `lineage.reused_existing_event_identities`;
- `lineage.new_event_identities`;
- first/last availability;
- per-run filings/events.

Only after normalization coverage is coherent do we build Event State V003.1.
