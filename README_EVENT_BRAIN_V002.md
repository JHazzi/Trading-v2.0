# Event Brain v0.2 — first capacity-controlled multi-company benchmark

## Files

- `models/events/dataset_v002.py`
  - target-asset state;
  - leave-one-out cross-sectional state;
  - sector-peer state;
  - event_state_v002 features.

- `evaluation/events/walkforward_v002.py`
  - event-grouped purged walk-forward folds;
  - horizon-overlap purge.

- `evaluation/events/audit_v002.py`
  - dataset diversity/concentration gates.

- `models/events/train_v002.py`
  - Market-only;
  - Event-only;
  - Market + Event residual;
  - capacity control;
  - contextual Event residual;
  - day-block bootstrap confidence intervals.

- `pipeline/event_brain_benchmark_v002.py`
  - audit / benchmark orchestration.

- `docs/EVENT_BRAIN_V002_DECISIONS.md`
  - architectural rationale and limitations.

## 1. Install

```bash
cd ~/quant_market_ai
unzip -o ~/Downloads/quant_market_ai_event_brain_v002.zip -d .
```

## 2. Compile

```bash
python -m py_compile \
  models/events/dataset_v002.py \
  evaluation/events/walkforward_v002.py \
  evaluation/events/audit_v002.py \
  models/events/train_v002.py \
  pipeline/event_brain_benchmark_v002.py
```

## 3. Tests

```bash
python -m pytest \
  tests/test_event_brain_v002_contract.py \
  tests/test_event_temporal_normalization_v002.py \
  tests/test_event_brain_v001_contract.py \
  -q
```

## 4. AUDIT FIRST

Do not train before this:

```bash
python -m pipeline.event_brain_benchmark_v002 \
  --stage audit \
  --horizons 1,3,5,10
```

Expected high-level gates:
- >= 200 model-ready rows;
- >= 8 assets;
- >= 5 event types;
- >= 180 distinct events;
- >= 365 days span;
- no single asset > 30%;
- no single event type > 45%.

If any horizon fails, do not lower the gate automatically.

## 5. First benchmark: 1-session only

If audit is green:

```bash
python -m models.events.train_v002 \
  --horizon-sessions 1 \
  --outer-folds 4 \
  --bootstrap-reps 2000
```

Send the complete JSON output back before running the other horizons.

The most important block is:

```text
comparisons.capacity_control_vs_contextual_event
```

Positive:

```text
mae_delta_baseline_minus_candidate_pct > 0
```

means event features reduced MAE versus a same-capacity market residual control.

The CI is more important than the point estimate.

## 6. Only after inspecting 1-session

Then run:

```bash
python -m pipeline.event_brain_benchmark_v002 \
  --stage benchmark \
  --horizons 3,5,10 \
  --outer-folds 4 \
  --bootstrap-reps 2000
```

## Outputs

Evaluation files are written under:

```text
reports/event_brain_v002/
models/events/artifacts/
```

Each horizon writes:
- pooled out-of-sample predictions CSV;
- JSON evaluation report;
- evaluation pickle artifact;
- `event_brain_training_runs` row in SQLite.

These are candidate/evaluation artifacts. Nothing is promoted to production.
