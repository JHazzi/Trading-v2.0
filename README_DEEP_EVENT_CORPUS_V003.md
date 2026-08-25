# Deep Event Corpus V003

Transforms the completed deep SEC document corpus into one homogeneous
2016–2026-style Event Brain research corpus.

## Important

These are SEC filing documents/evidence, **not general news**.

Do not retrain Event Brain until the final V003 science audit is reviewed.

## Install

```bash
cd ~/quant_market_ai
unzip -o ~/Downloads/quant_market_ai_deep_event_corpus_v003.zip -d .
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
  tests/test_deep_event_corpus_v003_contract.py \
  tests/test_event_temporal_normalization_v002.py \
  tests/test_event_brain_v002_contract.py \
  -q
```

## 1. Preflight

```bash
python -m pipeline.event_brain_deep_corpus_v003 \
  --stage preflight
```

Key fields:
- `common_start`
- `common_end_inclusive`
- `total_filings_in_common_window`
- `total_raw_documents_in_common_window`
- `raw_corpus_by_ticker`

The expected common start should be around September 2016 given the current
10-company price cohort. AAPL's older stored documents should not move it
backward.

## 2. One-ticker clustering smoke

```bash
python -m pipeline.event_brain_deep_corpus_v003 \
  --stage cluster \
  --ticker AAPL
```

Then:

```bash
python -m pipeline.event_brain_deep_corpus_v003 \
  --stage cluster-audit
```

Check:
- run completed;
- memberships approximately match documents considered;
- historical research memberships remain non-strict-PIT.

## 3. Full deep clustering

The completed AAPL run will be reused:

```bash
python -m pipeline.event_brain_deep_corpus_v003 \
  --stage cluster
```

Then:

```bash
python -m pipeline.event_brain_deep_corpus_v003 \
  --stage cluster-audit
```

Do not continue if any clustering run is not `completed`.

## 4. Normalize

```bash
python -m pipeline.event_brain_deep_corpus_v003 \
  --stage normalize
```

Stable SEC accession/item event identities from the pilot are reused. New
historical filings receive new event identities only when they represent new
economic events.

## 5. Rebuild Event State V003

```bash
python -m pipeline.event_brain_deep_corpus_v003 \
  --stage states
```

## 6. Rebuild labels

```bash
python -m pipeline.event_brain_deep_corpus_v003 \
  --stage labels
```

## 7. Final science audit

```bash
python -m pipeline.event_brain_deep_corpus_v003 \
  --stage audit
```

Send the complete JSON output before retraining.

The important outputs are:
- `lineage.unique_deep_events`
- `lineage.reused_existing_event_identities`
- `lineage.new_event_identities`
- `states_by_year`
- `states_by_ticker`
- `labels`
- `research_scale_target`
- `model_ready` for 1/3/5/10 sessions.

Only after this audit do we rerun the same Event Brain v0.2 evaluation contract.
