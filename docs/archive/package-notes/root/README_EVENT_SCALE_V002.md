# Event scale v002 — temporal normalization

The 10 SEC clustering runs are healthy:
- 1,850 memberships;
- 266 effective clusters;
- 0 strict-PIT historical memberships;
- AAPL reuses five pilot clusters instead of duplicating them.

This package fixes the last temporal issue before mass normalization.

## Why v001 is not used for the scaled corpus

`sec_event_normalizer_v001` selected metadata with:

    latest metadata observation available <= normalization run cutoff

For a historical filing that has an `unchanged` retrieval in August 2026, that
can select the 2026 observation even though the cluster evidence belongs to
2024/2025.

v002 instead selects:

    latest metadata observation available <= first evidence available_at

and sets event availability to:

    max(metadata_available_at, first_supporting_evidence_available_at)

Therefore an unchanged retrieval performed today cannot move a historical event
forward to today.

## Lineage versions

- `sec_event_normalizer_v002_temporal`
- `event_state_v002`
- `event_reaction_daily_v002`

Nothing from v001 is deleted or overwritten.

## Install and test

```bash
unzip -o quant_market_ai_event_scale_v002.zip -d ~/quant_market_ai
cd ~/quant_market_ai

python -m py_compile \
  ingestion/events/sec_event_normalizer_v002.py \
  features/events/event_state_v002.py \
  evaluation/targets/event_reaction_targets_v002.py \
  pipeline/event_brain_scale_normalize_v002.py

python -m pytest \
  tests/test_event_temporal_normalization_v002.py \
  tests/test_event_normalization_contract.py \
  tests/test_event_brain_v001_contract.py \
  -q
```

## Run scaled normalization

Run stages separately so each result can be inspected:

```bash
python -m pipeline.event_brain_scale_normalize_v002 --stage normalize
```

Then:

```bash
python -m pipeline.event_brain_scale_normalize_v002 --stage states
```

Then:

```bash
python -m pipeline.event_brain_scale_normalize_v002 --stage labels
```

Finally:

```bash
python -m pipeline.event_brain_scale_normalize_v002 --stage audit
```

Do not train yet. The next step after this audit is to upgrade the daily
Market-only context to explicit asset + cross-sectional + sector state and only
then benchmark Event Brain.
