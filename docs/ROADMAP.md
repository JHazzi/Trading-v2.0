# Research Roadmap

**Snapshot:** 2026-08-31
**Rule:** complexity is added only after the previous information contract earns it OOS.

## Active sequence after the 2026-08-27 checkpoint

```text
Event V0.2.1 robustness                    COMPLETE: conditional/unstable
Market scalar V003–V005.2                 COMPLETE: no promoted model
Distributional V006 empirical baseline    COMPLETE: scale information supported
V006.1 robustness/falsification           COMPLETE: vol63 strongest scale clue
V007 adaptive asymmetric scale            COMPLETE: rejected vs vol63
V008 conditional residual quantiles       COMPLETE: 4/4 significant failures
V008.1 endogenous closure                 COMPLETE: H1 developmental pass
V009 prospective temporal confirmation    ACTIVE: fit frozen; first seal pending >=2026-08-31
Temporal Dataset V001                     COMPLETE: parity PASS; raw-close long-horizon selection rejected
Temporal Dataset V002                     COMPLETE: economic/action review PASS
Temporal distributional V001              COMPLETE: closed negative vs vol63
Temporal distributional V002              COMPLETE: closed negative; holdouts sealed
Information Integration Readiness V001    COMPLETE: real read-only PASS
Public Information Intake V001            IMPLEMENTED: local tests PASS; remote intake user-run
Alpaca bars + priority news intake         NEXT: bars frozen; download/audit, then gated news
source semantics/reconciliation            AFTER corpus audit; no overwrite or median
Context information materializer          BLOCKED on source semantics; no training
distributional Event Brain                PARALLEL developmental track on existing SEC
identity hygiene                          REVIEW in parallel; no graph promotion
graph propagation                         BLOCKED until direct event increment
trajectory/risk/decision                  DEFERRED
```

V009 keeps raw vol63, the exact 14-feature V008.1 own-state family, the fixed
shallow profile and the no-calibration rule untouched. It uses one pre-holdout
fit and no refit during confirmation. Predictions must be sealed within 16
hours of the causal state close; missed eligible origins cannot be backfilled.
The first 126 resolved origins are descriptive only. The first 252 consecutive
eligible origins are the sole formal gate, using origin-day-equal pinball,
5/10/20-day moving blocks, five chronological stability blocks, quantile breadth
and calibration. Event Brain research may proceed separately but cannot
validate, rescue or change V009.

V001/V002 temporal modeling established that the current own-state information
does not beat `vol63 + tau` across a one-year horizon, even though the V002
residual is better than deranged-feature placebos and improves calibration.
Both branches are closed with interpolation holdouts sealed. The next parallel
development block inventories and gates new information before any additional
model is proposed.

## Phase 0 — Documentation checkpoint

**Status:** completed; canonical documents reconciled through the V006 checkpoint.

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

**Status:** completed; conditional/nonlinear candidate, not promoted.

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


<!-- EVENT_T0_V001_START -->
### Temporal integration with future Event Brain

Market V003 states are defined at exchange session close for all eligible
asset-days.

When Event Brain is reintroduced, an event at time `t` may only use the latest
Market Brain state/prediction with:

```text
market_state_time <= event_state_time
```

The event timestamp used for information availability is not automatically
the SEC acceptance time. Future multi-source event work will use the earliest
valid public evidence while preserving later confirmations as later evidence.
<!-- EVENT_T0_V001_END -->

## Phase 2 — Market Brain Daily V003

**Status:** scalar sequence V003–V005.2 closed as negative/inconclusive evidence.

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

**Status:** active; V006 empirical scale foundation passed its preregistered primary.

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

V001 completed but its HTTP-modified arrival clock was rejected; preserve its
output and do not train on or rebuild it. Corrected V002 completed its full
2,001-state materialization, temporal replay and exclusion review. D033 freezes
cross-accession/AAPL/early-Core exclusions without deleting source evidence or
retiming events. Follow
[DISTRIBUTIONAL_EVENT_DATASET_V002.md](DISTRIBUTIONAL_EVENT_DATASET_V002.md).

Current next substage: implement and run a **plan-only** Distributional Event
Brain preregistration. It must freeze one primary horizon/scenario, historical
per-fold Market Brain refits, a low-capacity event head, capacity-matched and
multi-seed placebo controls, the five purged V008.1 temporal windows,
origin-day weighting, proper distributional scores and moving-block
uncertainty before reading model performance. V009 remains unchanged.

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

### Horizon-conditioned terminal outcome foundation

V001 is complete as raw-close control/evidence. It reproduced H1/H3/H5/H10
exactly and found action overlap of 76.20%/79.09%/80.32% at H63/H126/H252.
Excluding those windows materially changes asset and sector selection, so V001
long-horizon raw-close outcomes are not the primary training target.

Temporal V002 total shareholder return is mechanically complete:

1. preserve every V001 raw target/status as control;
2. reconstruct `(Close_t + cash_t) / Close_(t-1)` from versioned action lineage;
3. retain split factors without applying them twice to provider-normalized Close;
4. use Adjusted Close only for a hard provider convention/unit/timing audit;
5. require full V001 parity and exact no-action H1/H3/H5/H10 identity;
6. report recovered/quarantined coverage by horizon, sector and origin year;
7. keep source/Core/V001 read-only and V009 isolated;
8. keep training blocked until full reports are reviewed and a model protocol
   is preregistered.

Use the 17 anchor/holdout checkpoints by default. Additional or dense taus are
supported from the shared grid, but do not create independent labels and may
not be selected after outcome inspection. A horizon-conditioned collection of
terminal marginals is still not a joint path model.

Current status: full local V002 build/audit and evidence-bound economic review
PASS with an empty downstream exclusion mask. Temporal Distributional V001 and
V002 both completed development and closed negative without opening the five
interpolation holdouts. The next step is not another rescue model: run
Information Integration Readiness V001, then review a shared context
materializer that preserves native asset/day/event units. The readiness audit
has now passed; materializer design/audit is the next step, not model fitting.

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
- unscoped/model-visible mass news ingestion without source rights/time/dedup gates;
- live auto-trading;
- “confidence” derived from RF tree dispersion;
- arbitrary Gaussian future-path simulation.
## Historical execution notes

The active sequence at the top governs current work. The chronological notes
below preserve earlier gates and decisions, including stages already closed.
They are not instructions to rebuild, refit or rescale. Use
[CONTEXT_RECOVERY.md](CONTEXT_RECOVERY.md) to verify actual persisted outputs.

<!-- MARKET_V003_BROAD_BACKFILL_V001 -->
## Market Daily V003 broad panel gate — 2026-08-25

Current gate:

```text
503 active equities known
10 quality-gated daily histories
BROAD_PANEL_BACKFILL_REQUIRED
```

Execution order:

1. discover per-asset first available Yahoo daily session and exchange;
2. audit the discovery manifest;
3. ingest a five-asset smoke batch through the existing append-only daily
   causal ingestion;
4. audit the smoke;
5. resume the full current-cohort backfill;
6. require a broad-panel audit before feature construction/training;
7. build Market V003 core all-asset-day features without external proxies;
8. test broad-market/sector/rate/volatility proxies later as incremental
   context, not as prerequisites.

Macro remains out until causal vintages exist.
<!-- MARKET_V003_CORE_DATASET_V001 -->
### Broad panel gate passed

Do not backfill more equities merely to obtain 503/503. Three assets remain
quality-quarantined while 500 clean assets provide a panel well above the
predeclared readiness gate.

Before model training:
1. materialize deterministic Market V003 core states;
2. materialize future labels separately from features;
3. audit leakage/coverage/sector missingness;
4. quantify corporate-action exclusion by horizon;
5. only then freeze the benchmark battery.

If H10 corporate-action exclusion is large, do not silently accept the
selection bias; evaluate a causally-defined total-return label version later.
<!-- MARKET_V003_BENCHMARK_V001 -->
### Market Daily V003 Benchmark

Run H1/H3/H5/H10 independently under the frozen benchmark plan.

Decision after results:

- if full Market V003 cannot consistently beat train-median / asset-mean
  baselines, improve market representation before Event Brain integration;
- if own-only works but cross-section/sector do not add value, do not keep
  context merely because it is architecturally appealing;
- if cross-section or sector adds paired OOS value, retain only the supported
  layers;
- only after this benchmark consider SPY/QQQ/IWM, volatility/rates, RF
  robustness, or distributional outputs.
<!-- MARKET_V003_RESULTS_V004_FACTORIZATION_V001 -->
### Phase 2 next step — Market Daily V004 factorization

V003 absolute-return benchmark is scientifically closed as a negative result.

Current sequence:

```text
V003 Core / Broad Panel                         DONE
V003 preregistered benchmark                    DONE
V003 primary absolute-return hypothesis        REJECTED
V004 factorization postmortem                  NEXT
V004 market/sector/asset component datasets    after postmortem
V004 component benchmarks                      after dataset audit
external proxies                               later incremental test
distributional Market Brain                    after stable point baseline
Event Brain integration                        after stable Market Brain
```
<!-- MARKET_V004_MATH_FOUNDATION_V001 -->
### Phase C refinement — Market Brain

```text
C1 V003 endogenous pooled baseline           REJECTED, retained
C2 V004 mathematical factorization           ACTIVE
C3 external market-state increments          NEXT if C2 is healthy
   - SPY / QQQ / IWM
   - sector ETFs
   - volatility
   - rates / credit
   - causal macro / regime
C4 distributional Market Brain               DEFERRED
D  Event Brain integration                    DEFERRED
```

C2 must separately evaluate market-factor, sector-residual, asset-residual
and reconstructed absolute-return performance. A component is kept only if it
adds paired out-of-sample value.
<!-- MARKET_V004_RESULTS_V005_EXTERNAL_STATE -->
## Market V004 factorized result and V005 external-state decision

V004 factorization materially improves V003 but does not pass the preregistered
absolute-return primary against fold train median at H1/H3/H5/H10. The loss
survives moving-block bootstrap.

Decision:

- retain V003 and V004 as canonical evidence;
- stop additional endogenous-price factorization as the primary research path;
- begin V005 incremental Market State enrichment;
- first increment is SPY/QQQ/IWM only;
- sector ETFs, volatility/rates/credit, macro, events and distributional heads
  remain separate later increments;
- no post-result tuning of V004.

This remains Architecture Phase C: improve the base Market Brain without news
before Event Brain integration.
<!-- MARKET_V0052_FINANCIAL_CONDITIONS_V001 -->
## Market Brain V005.2 — financial conditions

V005.1 SPY/QQQ/IWM did not pass its preregistered incremental gate over V004.
It is retained as negative/inconclusive evidence and is not stacked into the
next primary candidate.

V005.2 tests a more orthogonal Market State block:

```text
volatility: Cboe VIX (previous session only)
rates:      SHY / IEF / TLT
credit:     HYG / LQD
```

The V004 factorized Market Brain remains the frozen control.

Causal clock:
- same-day ETF closes are available at the equity-session origin;
- same-day daily VIX close is NOT used because Cboe VIX RTH continues after
  the equity close; VIX features use one full session lag;
- historical reference data remains research backfill, strict PIT=false;
- Yahoo adjusted_close is not used for ETF state features;
- ETF returns are reconstructed from Close plus only cash distributions whose
  effective trading day has occurred, then compounded over 5/20 sessions.

Primary candidate is the complete financial-conditions block. VIX-only,
rates-only and credit-only candidates are preregistered diagnostics and cannot
rescue a failed primary after results.

No sector ETFs, macro vintages, Event Brain, graph, distributional heads,
regime-conditioned training or hyperparameter tuning enter V005.2.
<!-- EVENT_GRAPH_BRAIN_FOUNDATION_V001 -->
## Event–Graph Brain Foundation V001

Market Brain V004 is retained as the frozen structural prior/control. V005.1
and V005.2 remain evidence about market-context information and are not stacked
into Event–Graph Brain.

The next architecture work resumes phases D/E:

```text
evidence -> event -> entity
relation evidence -> temporal structural graph
event + G_t -> asset exposure candidates
```

New canonical contract: `docs/EVENT_GRAPH_CONTRACTS.md`.

Foundation rules:

- candidate extraction is not model-visible until resolution/promotion;
- structural relation evidence must satisfy `available_at <= t`;
- graph propagation nominates potentially exposed assets but assigns no market
  direction or predictive weight;
- structural graph is first; statistical/learned graph and GNN are deferred;
- foundation propagation is one hop;
- evaluation is nested:
  `V004+direct event vs V004`, then
  `V004+direct event+graph vs V004+direct event`;
- graph claims require negative controls, including matched unconnected assets
  and future-evidence leakage checks.

No Event–Graph predictive model is trained in the foundation package.

<!-- MARKET_DIST_V0061_ROBUSTNESS_V001_START -->
## V006.1 execution contract

Before learned distributional modeling, run the frozen V006.1 robustness package. Required outputs are exact V006 reproduction, tail-specific diagnostics, direct `asset_empirical` comparison, asset/sector concentration and leave-one-out sensitivity, train-defined volatility regimes, calibration drift blocks, and predeclared `vol5`/`vol63` scale sensitivities.

V006.1 has no promotion gate for an alternative scale. Its role is to determine the scope and failure modes of the existing V006 claim. Only after interpreting all four horizons should a separately versioned learned distributional Market Brain be preregistered.
<!-- MARKET_DIST_V0061_ROBUSTNESS_V001_END -->

<!-- MARKET_DIST_V007_ADAPTIVE_TAIL_V001_START -->
## Learned Distributional Market Brain — V007 active

V006.1 robustness is complete. The next active experiment is V007 adaptive asymmetric asset-scale.

Required design:
- reuse the Core V003 causal daily panel and V003 outer purged folds;
- nested temporal selection inside each outer train;
- q50 fixed to global train median, so no directional-location claim enters this increment;
- asset-specific empirical tail shape as structural anchor;
- dynamic state limited to causal vol20 and vol63 normalized by each asset's training medians;
- separate downside/upside scale parameters;
- primary comparison against `vol63_scaled_empirical`;
- secondary comparisons against V006 vol20, `asset_empirical` and global empirical;
- pinball plus quantile calibration primary diagnostics, Brier/median MAE still reported;
- all four horizons mandatory.

A strong V007 result can justify a richer learned quantile model later. It cannot by itself count as prospective confirmation because V006.1 informed the mathematical hypothesis.
<!-- MARKET_DIST_V007_ADAPTIVE_TAIL_V001_END -->

<!-- MARKET_DIST_V007_ZERO_VOL_AMENDMENT_V0011_START -->
## V007 pre-performance implementation amendment — exact zero volatility

The first V007 H1 benchmark attempt aborted during data loading before any OOS metric was produced. Core V003 permits exact zero rolling volatility, while the initial V007 loader incorrectly required both vol20 and vol63 to be strictly positive.

V007.0.1 corrects only this domain handling. Exact observed zero volatility is mapped to the already-frozen lower log-ratio clip; a nonpositive per-asset TRAIN median normalizer falls back to the positive global TRAIN median. The vol20 control restores the completed V006 `global_empirical_fallback` behavior for nonpositive scale rows, and vol63 uses the same prospective control rule. Negative/null volatility remains a hard error.

No rows are dropped, no epsilon is introduced, and no alpha/lambda/kappa grid, feature, primary reference, quantile, horizon, fold or score is changed. The plan gate now reports zero/negative/null scale support from the real Core V003 DB. This amendment must be committed before rerunning V007.
<!-- MARKET_DIST_V007_ZERO_VOL_AMENDMENT_V0011_END -->

<!-- MARKET_DISTRIBUTIONAL_V008_V001_START -->
## Learned Distributional Market Brain V008 — conditional residual information gate

Sequence after V007:
```text
V006 empirical volatility scale                 SUPPORTED
V006.1 robustness / vol63 sensitivity           COMPLETE
V007 handcrafted adaptive asymmetric scale      REJECTED
V008 conditional residual quantile learner      NEXT
```

V008 freezes `vol63_recent_calibrated` as the primary reference. Candidate and reference receive the same recent train-only calibration opportunity. Hyperparameter profile selection is nested temporally. The Core V003 feature schema is resolved without outcomes during `--stage plan`, persisted as `resolved_feature_manifest.json`, and must be committed before benchmarking.

Decision branch:
- if V008 beats the calibrated vol63 reference with acceptable calibration across horizons, retain the learned endogenous distributional Market Brain and test new information blocks incrementally;
- if V008 does not, stop increasing endogenous learner capacity and prioritize causally versioned information that a professional investor would actually use but Core V003 lacks: expectations/revisions, option-implied risk, fundamentals/valuation, positioning/flows and richer event surprise, one block at a time;
- Event Brain, graph, trajectories and trading remain downstream of a credible base distribution.
<!-- MARKET_DISTRIBUTIONAL_V008_V001_END -->

<!-- EXPECTATION_CAPTURE_FOUNDATION_V001_START -->
## Parallel track — strict-PIT expectation/information capture

This track may run while V009 accumulates because it is data-only and isolated from V009. Order:

```text
capture contract + isolated DB           ACTIVE
provider/source semantic audit           NEXT
prospective scheduled/expectation capture AFTER provider contract
feature derivation                        BLOCKED until preregistered experiment
predictive use                            BLOCKED until incremental Event/Information gate
```

No provider is promoted by convenience, no historical backfill is relabeled strict PIT, and no captured field enters a predictor without a later preregistered incremental-information experiment.
<!-- EXPECTATION_CAPTURE_FOUNDATION_V001_END -->

<!-- product-track-v0 -->
## Parallel Product Track

V009 remains frozen as a prospective scientific claim. Product work proceeds independently through Investment Workbench V0, Decision Journal, paper decision mode, risk/cost integration and later a gated Decision Engine. Product consumers may read published model artifacts but must not alter V009's frozen contract.

<!-- temporal-distributional-runner-v001 -->
## Closed temporal modeling and active information escalation

```text
V002 economic entitlement review                 PASS
extreme-tail lineage audit (80 outcomes)         PASS
versioned selection mask (0 exclusions)          PASS
Temporal Distributional V001                     CLOSED NEGATIVE
Temporal Distributional V002                     CLOSED NEGATIVE
H7/H17/H42/H90/H180 evaluation                   SEALED / NEVER OPENED
Information Integration Readiness V001           REAL READ-ONLY PASS
shared context materializer                      NEXT: design/audit
incremental Context V003 preregistration         BLOCKED on materializer audit
```

No dense H1..H252 table or 252-head model is planned. V002 can technically query
integer tau up to one year, but neither tested temporal model earned the right
to open interpolation holdouts. New context must demonstrate incremental
information one block at a time before trajectories, alpha or production can
be discussed.
