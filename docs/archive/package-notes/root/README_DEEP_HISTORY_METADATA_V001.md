# Deep SEC history — metadata stage v0.1

This package starts the next scale step without changing the Event Brain model.

It expands the same 10-company cohort backward in SEC metadata, using up to
250 relevant filings per issuer target.

XOM is handled through explicit issuer continuity:
- successor/current registrant CIK 2115436;
- historical predecessor CIK 34088.

This stage is metadata-only by design. Do NOT mass-download/normalize the deep
history until the coverage audit is reviewed and the duplicate-normalization
guard is added.

## Install

```bash
cd ~/quant_market_ai
unzip -o ~/Downloads/quant_market_ai_deep_history_metadata_v001.zip -d .
```

## Compile + tests

```bash
python -m py_compile \
  ingestion/events/sec_edgar_v4_history.py \
  pipeline/event_brain_deep_history_v001.py

python -m pytest \
  tests/test_sec_edgar_v4_history_contract.py \
  tests/test_sec_metadata_v3_contract.py \
  -q
```

## Preflight

```bash
python -m pipeline.event_brain_deep_history_v001 --stage preflight
```

## Metadata scale

Use your existing SEC_USER_AGENT environment variable, then run:

```bash
python -m pipeline.event_brain_deep_history_v001 --stage metadata
```

## Audit

```bash
python -m pipeline.event_brain_deep_history_v001 --stage audit
```

Send the complete audit output back before downloading deep-history documents.

We will use the actual `by_ticker`, `by_year`, `by_form`, and `by_cik`
coverage to decide whether 250 is enough and to build the next document/event
scale stage without duplicating the pilot Event States.
