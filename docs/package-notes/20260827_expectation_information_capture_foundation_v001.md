# Expectation / Information Capture Foundation V001

Date: 2026-08-27

## Why this exists

V009 is an untouched prospective Market Brain holdout and must remain frozen. Parallel work should therefore create infrastructure that cannot alter its fit, state, predictions or gate.

The highest-value parallel asset is a strict-PIT observation history for information that is difficult or impossible to reconstruct perfectly later: expectations, guidance snapshots, scheduled-event revisions and eventual economic facts.

This package creates only a **capture foundation**. It does not train V010, alter V009, declare Event Brain alpha, or choose an analyst/options provider.

## Isolation rule

Default database:

```text
data/database/information_capture_v001.db
```

It refuses `market_data.db` and does not modify Market Core tables.

## Stages

```bash
python -m pipeline.expectation_information_capture_v001 --stage plan
python -m pipeline.expectation_information_capture_v001 --stage init
python -m pipeline.expectation_information_capture_v001 --stage audit
python -m pipeline.expectation_information_capture_v001 --stage manifest
```

A provider adapter or manually captured payload can later be imported from JSONL:

```bash
python -m pipeline.expectation_information_capture_v001 \
  --stage ingest-jsonl \
  --input /path/to/capture.jsonl
```

## What NOT to do

- Do not join this DB into V009.
- Do not refit V009.
- Do not call historical API snapshots strict PIT merely because they carry an old `as_of` field.
- Do not compute a surprise feature using an expectation snapshot captured after the actual was public.
- Do not choose a provider based on which one makes historical backtests look best.

## Recommended next research step after installation

Do not ingest hundreds of sources yet. First audit which of these source classes can be obtained with honest time semantics:

1. scheduled company events / IR calendar;
2. company guidance observations;
3. analyst consensus/revisions;
4. option-implied distribution observations;
5. macro release expectations/vintages.

For each, write a provider/source contract before code.
