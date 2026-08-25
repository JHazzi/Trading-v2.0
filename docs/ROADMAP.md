# Research Roadmap

**Snapshot:** 2026-08-25  
**Rule:** complexity is added only after the previous information contract earns it OOS.

## Phase 0 — Documentation checkpoint

**Status:** current cleanup.

Deliverables:

- canonical root docs;
- current research status;
- decision log;
- experiment registry;
- temporal/data contracts;
- documentation policy;
- historical package docs archived.

Exit gate:

- no conflicting root `README_*` files;
- `docs/INDEX.md` identifies current sources of truth.

## Phase 1 — Event Brain V0.2.1 Robustness

**Goal:** attempt to falsify the H10 candidate signal without changing the event corpus or inventing richer features.

Required checks:

### Baselines

- zero;
- train mean;
- train median;
- always up;
- always down;
- train-only majority direction.

### Simple model families

- Ridge;
- ElasticNet;
- Huber;
- current RF.

### Stochastic stability

Run a predeclared seed set. Do not select the best seed.

### Dependence-aware evaluation

- group or sensitivity by SEC accession/filing;
- event identity grouping;
- horizon-aware block bootstrap for H10;
- origin-day dependence;
- overlapping-return sensitivity.

### Subgroups / concentration

- folds;
- year/regime;
- asset;
- event type;
- filing form;
- high-volatility/extreme days.

### Early-OOS sensitivity

Current primary folds begin around 2021. Add a secondary design that tests earlier regimes where scientifically feasible without weakening training gates.

### Exit interpretation

**A. H10 survives broadly:** continue treating event information as a serious candidate, without calling it production alpha.

**B. H10 collapses:** freeze scalar Event Brain result as null/unstable and continue with stronger Market Brain/distributional event questions rather than tuning scalar RF.

No additional SEC scale is needed for either outcome.

## Phase 2 — Market Brain Daily V003

**Goal:** build a stronger base market state before richer event conditioning.

Candidate strictly-as-of context:

- SPY/broad-market returns and volatility;
- QQQ/style;
- IWM/small-cap context;
- sector ETFs or robust sector composites;
- VIX / volatility regime;
- Treasury yields;
- slope/curve features;
- breadth;
- liquidity/volume regime;
- market drawdown/regime;
- optional macro release-state features only with vintage/release semantics.

Do not add a Transformer simply because more features exist.

Evaluation:

- same temporal split discipline;
- zero/mean/median/simple linear baselines;
- multiple horizons;
- multi-seed where stochastic;
- calibration readiness.

Exit gate:

> The daily Market Brain must show consistent OOS value versus trivial baselines, not only on event days.

## Phase 3 — Distributional Market Brain

**Goal:** estimate calibrated future uncertainty rather than point-return only.

Minimum outputs:

```text
q05 q25 q50 q75 q95
P(return > 0)
P(return > costs)
downside tail probabilities
```

Candidate first methods:

- quantile regression;
- quantile forests/boosting where justified;
- conformal interval calibration;
- simple distributional models;
- probability heads with calibration.

Metrics:

- pinball loss;
- interval coverage/sharpness;
- Brier score;
- calibration curves;
- CRPS if a coherent distribution is available.

Exit gate:

- calibrated OOS probabilities/intervals that beat simple distributional baselines.

## Phase 4 — Distributional Event Brain

**Goal:** test whether the existing SEC Event State changes the future distribution.

Use the current event corpus first.

Questions:

- Does `E` improve q50?
- Does `E` improve q05/q95?
- Does `E` improve interval width?
- Does `E` improve downside-tail probability?
- Does `E` predict realized path volatility?
- Does `E` improve MFE/MAE estimates?
- Is information useful at different horizons for different targets?

The current labels already store:

```text
return_pct
mfe_pct
mae_pct
realized_path_vol_pct
```

Exit gate:

- reproducible incremental proper-score improvement relative to capacity-matched/no-event controls.

## Phase 5 — Rich Event Semantics

Only after the distributional question is understood.

Add:

- filing text embeddings or structured extraction;
- numeric facts;
- changes from prior filings;
- guidance;
- actual vs expectations;
- analyst consensus;
- surprise;
- novelty/corroboration;
- claim epistemic type.

Do not encode fixed bullish/bearish lookup tables.

## Phase 6 — Additional information sources

Potential sources:

- company Investor Relations;
- earnings releases;
- presentations;
- reputable wires/news;
- analyst estimates/revisions;
- macro releases.

Requirements:

- immutable raw document;
- publication/retrieval/availability timestamps;
- source identity;
- deduplication/clustering;
- event linkage;
- no source-reliability hardcoding.

## Phase 7 — Graph

Only after local event information has demonstrated value.

Layers:

1. structural;
2. statistical;
3. learned.

Relationships are temporal/versioned and evidence-backed.

No co-occurrence shortcut.

## Phase 8 — Trajectory / Risk / Decision

Build model-derived future path scenarios.

Then risk, configurable costs, slippage/spread, decision policy and paper trading.

Prediction and decision remain separate.

## Phase 9 — Controlled continuous learning

Persist all predictions.

Implement:

```text
prediction
→ outcome
→ calibration/error
→ drift
→ candidate
→ walk-forward
→ champion comparison
→ promotion/rejection
→ rollback
```

No blind online self-modification.

## Explicitly deferred

Do not prioritize yet:

- large neural sequence models;
- reinforcement-learning trading policy;
- graph neural networks;
- massive news ingestion;
- live auto-trading;
- “confidence” derived from RF tree dispersion;
- arbitrary Gaussian future-path simulation.
