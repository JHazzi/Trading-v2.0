# Event Brain Deep Benchmark V003.1

## Install

```bash
cd ~/quant_market_ai
unzip -o \
  ~/Downloads/quant_market_ai_event_brain_deep_benchmark_v0031.zip \
  -d .
```

## Compile + tests

```bash
python -m py_compile \
  models/events/train_v0031_deep.py \
  pipeline/event_brain_deep_benchmark_v0031.py

python -m pytest \
  tests/test_event_brain_deep_benchmark_v0031.py \
  tests/test_event_brain_v002_contract.py \
  -q
```

## Audit the exact dataset used by the trainer

```bash
python -m pipeline.event_brain_deep_benchmark_v0031 \
  --stage audit \
  --horizons 1,3,5,10
```

The contract must say:

```text
event_feature_version = event_state_v0031_deep
label_version = event_reaction_daily_v0031_deep
model_version = event_brain_v002_architecture_on_deep_v0031
```

Expected row scale from the completed corpus audit:

```text
H1  ~1700
H3  ~1667
H5  ~1619
H10 ~1353
```

Do not benchmark if it falls back to roughly 200–300 rows.

## Primary hypothesis: H10 first

```bash
python -m pipeline.event_brain_deep_benchmark_v0031 \
  --stage benchmark \
  --horizons 10 \
  --outer-folds 4 \
  --bootstrap-reps 2000
```

Send the complete H10 output before running the other horizons.

## Negative-control horizons after H10 review

```bash
python -m pipeline.event_brain_deep_benchmark_v0031 \
  --stage benchmark \
  --horizons 1,3,5 \
  --outer-folds 4 \
  --bootstrap-reps 2000
```

Outputs go under `reports/event_brain_v0031_deep/` and cannot overwrite the
pilot reports.
