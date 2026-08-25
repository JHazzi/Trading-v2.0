# Event Brain V0.2.1 Robustness — Pre-registered falsification package

**Date:** 2026-08-25  
**Purpose:** attempt to falsify the weak H10 candidate found by the frozen V0.2 architecture on Deep Event Corpus V003.1.

This package does **not** add data, event features, model tuning, news, graph features or a stronger Market Brain.

It is intentionally evaluation-only and does not write model-training rows to the database.

## Frozen scientific question

Primary horizon:

```text
10 sessions
```

Primary comparison:

```text
capacity-control residual
vs
contextual residual (market + event)
```

Positive MAE delta means the contextual candidate has lower MAE.

The existing deep benchmark was approximately:

```text
delta = +0.02819 percentage points
95% origin-day bootstrap CI ≈ [-0.00366, +0.05968]
4/4 folds positive
```

The goal of V0.2.1 is **not** to increase that number.

The goal is to see whether the sign/effect survives alternative reasonable assumptions.

## Frozen robustness battery

### A. Random-Forest seed stability

No best-seed selection.

```text
7
17
42
123
2026
```

Same RF hyperparameters as V0.2:

```text
n_estimators = 400
min_samples_leaf = 6
max_features = 0.8
```

### B. Simple model families

No hyperparameter search.

```text
Ridge(alpha=1.0)
ElasticNet(alpha=0.01, l1_ratio=0.5)
Huber(epsilon=1.35, alpha=0.0001)
```

Each family uses the same conceptual capacity-control/contextual residual comparison.

### C. Dependence-aware uncertainty

Report all of:

1. existing independent-origin-day bootstrap;
2. SEC-accession cluster bootstrap;
3. moving-block bootstrap over ordered origin days.

Moving-block lengths:

```text
5
10  ← primary sensitivity
20
```

Bootstrap repetitions:

```text
5000
```

The moving blocks use consecutive **observed event-origin days**, not a claim of exact exchange-session blocks. This is a dependence sensitivity, not a perfect generative model.

### D. Filing/accession grouping

The SEC stable event identity is:

```text
sec:<accession>:<item/form identity>
```

One filing can generate multiple event identities. The structural sensitivity therefore rebuilds both outer folds and inner OOF residual folds using `accession_number` as the grouping unit.

This is stricter than event-id grouping.

### E. Earlier OOS sensitivity

Primary split keeps:

```text
initial_fraction = 0.45
```

Secondary early-OOS sensitivity uses:

```text
initial_fraction = 0.30
```

with no parameter retuning.

This is intended to expose more earlier regimes to OOS evaluation.

### F. Concentration and extreme-outcome sensitivity

From OOS predictions:

- leave one asset out at evaluation time;
- leave one sufficiently represented event type out;
- remove only the largest absolute-return 1% / 2.5% as an outcome-conditioned sensitivity.

These are diagnostics, not proposed production filters.

### G. Baselines

Point baselines:

```text
zero
train mean
train median
```

Direction-only baselines:

```text
always up
always down
train-only majority direction
```

Do not interpret `sign(0)` as a meaningful directional baseline.

## What is NOT allowed before seeing results

Do not:

- tune RF hyperparameters;
- change seed list;
- choose the best seed;
- choose the best simple model and report only it;
- change block lengths because one interval is nicer;
- add features;
- add/remove assets;
- add more SEC history;
- add news;
- change target;
- winsorize the training target.

If a bug is discovered, fix the bug and document the change before re-running.

## Run order

### 1. Install

```bash
cd ~/quant_market_ai

unzip -o \
  ~/Downloads/quant_market_ai_event_brain_v0021_robustness.zip \
  -d .
```

### 2. Compile and test

```bash
python -m py_compile \
  evaluation/events/robustness_v0021.py \
  models/events/robustness_v0021.py \
  pipeline/event_brain_robustness_v0021.py

python -m pytest \
  tests/test_event_brain_robustness_v0021.py \
  tests/test_event_brain_v002_contract.py \
  tests/test_event_brain_deep_benchmark_v0031.py \
  -q
```

### 3. Run the read-only scientific audit first

```bash
python -m pipeline.event_brain_robustness_v0021 \
  --stage audit
```

Inspect/send this output before the expensive model stages.

Expected high-level scale:

```text
horizon = 10
rows ≈ 1353
unique_events ≈ 1314
unique_accessions < unique_events
baseline deep report contract = PASS
```

### 4. RF seed stability

```bash
python -m pipeline.event_brain_robustness_v0021 \
  --stage rf-seeds
```

### 5. Simple models

```bash
python -m pipeline.event_brain_robustness_v0021 \
  --stage simple-models
```

### 6. Structural/dependence sensitivity

```bash
python -m pipeline.event_brain_robustness_v0021 \
  --stage structural
```

### 7. Consolidate only after all prior stages

```bash
python -m pipeline.event_brain_robustness_v0021 \
  --stage summary
```

Reports:

```text
reports/event_brain_v0021_robustness/
```

## Interpretation policy

There is no single automatic “signal passed” threshold in this package.

We will interpret the complete pattern:

- sign stability across all predeclared seeds;
- whether simple estimators see a similar incremental effect;
- accession-grouped result;
- early-OOS result;
- block/accession bootstrap intervals;
- leave-one-asset/type sensitivity;
- fold consistency;
- baseline quality.

A result is scientifically more credible when it survives these changes **without selecting favorable variants after the fact**.
