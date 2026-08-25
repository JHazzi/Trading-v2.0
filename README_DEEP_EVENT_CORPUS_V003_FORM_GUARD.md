# Deep Event Corpus V003 — form guard patch

## Why this patch exists

The AAPL smoke clustered 848 SEC raw documents while the V003 cohort preflight
reported 845 raw documents. The +3 delta matches legacy AAPL Form 4 material
already present in the database.

Nothing is corrupted and the AAPL clustering run does not need to be deleted.

The deterministic clustering source adapter is intentionally generic SEC code,
so it can see SEC documents outside this experiment's selected form family.

This patch makes the research boundary explicit at normalization time:

    allowed forms:
      8-K, 8-K/A, 10-Q, 10-Q/A, 10-K, 10-K/A

A Form 4 may remain safely stored and clustered, but cannot create a V003 event.

The patch also captures deterministic-clustering stdout so multi-ticker runs no
longer dump thousands of assignment rows to the terminal. Only concise pipeline
summaries are shown.

## Install

```bash
cd ~/quant_market_ai
unzip -o ~/Downloads/quant_market_ai_deep_event_corpus_v003_form_guard.zip -d .
```

## Compile

```bash
python -m py_compile \
  ingestion/events/sec_event_normalizer_v003_deep.py \
  pipeline/event_brain_deep_corpus_v003.py
```

## Focused tests

```bash
python -m pytest \
  tests/test_deep_event_corpus_v003_form_guard.py \
  tests/test_deep_event_corpus_v003_contract.py \
  -q
```

There is no need to rerun the previous long AAPL clustering smoke.

## Full clustering

The completed AAPL run is automatically reused:

```bash
python -m pipeline.event_brain_deep_corpus_v003 \
  --stage cluster
```

Then:

```bash
python -m pipeline.event_brain_deep_corpus_v003 \
  --stage cluster-audit
```

Expected:
- 10 completed deep clustering runs;
- AAPL reused;
- corpus clustering audit PASS;
- PIT memberships remain 0 for historical reconstructed evidence;
- `cluster_vs_cohort_document_delta.AAPL.delta` may remain +3, but those forms
  are now quarantined before event identity creation.

Do NOT normalize until the 10-run cluster audit has been reviewed.
