# Quant Market AI — Architecture v0.2

**Status:** canonical architecture  
**Last major review:** 2026-08-25

This file describes the intended system architecture. It is deliberately more stable than implementation notes, package READMEs and experiment reports. Current empirical status belongs in `docs/RESEARCH_STATUS.md`; near-term work belongs in `docs/ROADMAP.md`.

## 1. Purpose

Quant Market AI is a research system for learning how a causal market state evolves and for producing **probabilistic future trajectories**, not a single deterministic price target.

Conceptually:

\[
P(R_{t:t+T}\mid X_t,E_t,G_t,T)
\]

- `X_t`: market state known at time `t`;
- `E_t`: event/information state available at `t`;
- `G_t`: relationship/graph state available at `t`;
- `T`: requested horizon.

The system must not assume that returns are normal, uncertainty follows a universal square-root-of-time law, a document is itself an event, or economic impact/reliability can be safely hardcoded.

## 2. Non-negotiable principles

### 2.1 Observed market outcome is ground truth

The actual observed price/return/path is the truth about what the market did. A source, model or interpretation may be wrong.

This does **not** mean observed price equals intrinsic value.

### 2.2 Market Brain works without news

The base model must produce a forecast from market/history/context alone. Event/news information can modify the forecast but cannot be required for the system to function.

### 2.3 Documents are evidence; events are economic objects

Many documents can support one economic event. One filing can also contain multiple economically distinct events.

```text
source documents
      ↓
evidence / clustering
      ↓
normalized economic events
      ↓
causal event state
```

### 2.4 Observation, inference, prediction and outcome stay separate

Never store a learned property as if it were an observed fact.

At minimum distinguish raw observation, normalized fact/identity, derived feature/state, model output, prediction, realized outcome and evaluation.

### 2.5 Learn economic behavior instead of hardcoding it

Do not manually freeze source reliability, economic importance, market direction, persistence/decay, relation strength, sensationalism, novelty or propagation strength when they can be learned.

Deterministic taxonomy and data-integrity rules are allowed.

### 2.6 Causality is a data contract

For a prediction at time `t`, every feature must be reproducibly derivable from information with a legitimate availability boundary no later than `t`.

`available_at` is the primary feature gate.

### 2.7 Historical reconstruction is not strict PIT

A filing downloaded in 2026 can be used in historical research with a reconstructed historical availability proxy only if that distinction is explicit.

Keep separate historical/public availability proxy, actual retrieval/observation time and strict point-in-time flag.

Never falsify retrieval timestamps to make a backfill appear PIT.

### 2.8 Walk-forward before profitability

Primary evaluation is temporal and out-of-sample. Random splits are diagnostic at most.

### 2.9 Reproducibility

Every model result must identify model version, feature/state version, label version, dataset selection contract, temporal split/fold contract, random seed(s) and evaluation metric definitions.

### 2.10 Controlled learning, not blind online mutation

Long-term loop:

```text
prediction
→ outcome
→ error/calibration/drift diagnosis
→ candidate training
→ walk-forward evaluation
→ champion/candidate comparison
→ promotion or rejection
→ rollback capability
```

No blind `partial_fit` or automatic production mutation from a small rolling error window.

## 3. Logical architecture

```text
                           RAW OBSERVATIONS
              prices / SEC / news / macro / schedules
                                   │
                                   ▼
                      NORMALIZATION / ENTITIES
                                   │
                     ┌─────────────┴─────────────┐
                     ▼                           ▼
                MARKET FACTS                EVENT EVIDENCE
                     │                           │
                     ▼                           ▼
               MARKET STATE X(t)           EVENT STATE E(t)
                     │                           │
                     └─────────────┬─────────────┘
                                   │
                         optional GRAPH G(t)
                                   │
                                   ▼
                       BASE MARKET DISTRIBUTION
                         F0(Y | X, T)
                                   │
                                   ▼
                     EVENT-CONDITIONED DISTRIBUTION
                        F1(Y | X, E, G, T)
                                   │
                                   ▼
                   CALIBRATION / RISK REPRESENTATION
                                   │
                                   ▼
                         DECISION / COST LAYER
                                   │
                                   ▼
                       REPORT / PAPER TRADING
                                   │
                                   ▼
                              OUTCOMES
                                   │
                                   ▼
                       DIAGNOSTICS / CANDIDATES
```

The Event Brain does not need to remain an additive residual forever. The architectural contract is **incremental information relative to the Market Brain**, not one specific estimator topology.

## 4. Market Brain

### 4.1 Responsibility

Estimate the base future distribution using no event/news features:

\[
F_0(Y\mid X_t,T)
\]

`Y` should eventually describe more than terminal return.

### 4.2 Market state

Market state should progressively include strictly-as-of versions of:

- multi-scale returns;
- momentum/trend;
- realized volatility/range;
- volume/liquidity;
- drawdown and distance to extrema;
- broad index context;
- style/factor context;
- sector context;
- rates/yield-curve context;
- volatility regime;
- market breadth;
- macro regime;
- graph-derived context when mature.

Indicators such as RSI/ATR are optional derived features, not privileged truths.

### 4.3 Current research gap

The current daily market context is intentionally simple and does not consistently outperform trivial zero/median baselines. `docs/RESEARCH_STATUS.md` is authoritative for the latest numbers.

The next Market Brain milestone is better causal context, not a larger neural network.

## 5. Event / Information Brain

### 5.1 Responsibility

Measure whether information available at `t` changes the conditional future distribution.

Compare:

\[
F_0(Y\mid X_t,T)
\]

against:

\[
F_1(Y\mid X_t,E_t,G_t,T)
\]

The scientific object is the **incremental information gain**.

### 5.2 Possible effects

An event can change any combination of:

- median/expected return;
- probability of positive/negative return;
- distribution width;
- downside/upside tails;
- path volatility;
- MFE/MAE;
- regime probability;
- persistence of uncertainty.

An event may be economically important even when it does not improve point-return MAE.

### 5.3 Current state

The deep SEC corpus validates causal document→event→state→label infrastructure. The first scalar benchmark is a deliberately minimal experiment and is not the final Event Brain architecture.

See `ARCHITECTURE_EVENT_LAYER.md`.

## 6. Distributional targets

The end product should not be RF tree dispersion treated as market uncertainty.

Minimum useful output for horizon `T`:

- `q05`, `q25`, `q50`, `q75`, `q95`;
- `P(R_T > 0)`;
- `P(R_T > costs)`;
- downside-tail probabilities;
- path-volatility estimate/distribution;
- MFE/MAE estimates;
- calibration metadata.

Candidate evaluation:

- MAE for median/point location;
- pinball loss for quantiles;
- Brier score for probabilities;
- CRPS or another proper distributional score where appropriate;
- calibration error/reliability curves;
- coverage and sharpness for intervals.

## 7. Time and trajectories

Horizon is part of the learned problem.

Initial practical implementations may use discrete horizons, but they should not extrapolate one fixed horizon with universal scaling laws.

The desired UI/decision object is a **distribution of plausible paths**, not an arbitrary Gaussian random walk around a point forecast.

Long-term trajectory modeling must preserve temporal dependency between intermediate horizons.

## 8. Event semantics and expectations

Current SEC taxonomy provides factual event categories and causal evidence lineage.

Future representation should learn richer context such as:

- numeric filing facts;
- changes from prior values;
- guidance;
- analyst/company expectations;
- actual-vs-expectation surprise;
- novelty;
- corroboration;
- claim epistemic type;
- information already priced into market state.

Never encode rules such as `earnings beat = bullish`.

## 9. Reliability and predictive utility

Separate concepts that are often incorrectly merged:

- factual reliability;
- novelty;
- predictive relevance;
- market impact;
- persistence;
- source/context calibration.

A perfectly factual SEC filing can have near-zero predictive utility if the market already expected the information.

## 10. Graph architecture

Do not build a single undifferentiated graph.

Future graph layers:

1. **Structural** — ownership, suppliers, customers, competitors, index/ETF membership, regulatory exposures.
2. **Statistical** — correlations, lead/lag and conditional dependencies.
3. **Learned** — repeatedly supported relationships not explicitly encoded.

Every learned relation should be time-aware and versioned with evidence, confidence, latency and revalidation history.

Co-occurrence in documents is evidence of association, not proof of causal propagation.

Graph propagation should be added only after local event information is demonstrated to be useful.

## 11. Risk and decision layers

Prediction and decision are separate.

Prediction:

> What future distribution is plausible?

Risk:

> What loss/tail/path risk follows from that distribution?

Decision:

> Given expected return, uncertainty, costs, liquidity, position constraints and alternatives, is an action justified?

Broker fees, spread, slippage and taxes belong here, not inside the predictive state.

A binary `U*P - D*(1-P) - cost` equation is not the universal system model.

## 12. Temporal/data contracts

Canonical timestamp semantics live in `docs/DATA_CONTRACTS.md`.

Core rule:

```text
feature usable at prediction t
iff
legitimate available_at <= t
```

Future-derived values belong only to outcomes/labels.

Event clustering itself must respect temporal availability.

## 13. Evaluation

Primary evaluation must include:

- expanding/rolling walk-forward;
- purging outcomes that overlap test start;
- grouping to prevent event identity leakage;
- simple baselines;
- capacity/negative controls when measuring incremental information;
- paired comparisons;
- dependence-aware uncertainty estimates;
- multi-seed stability for stochastic models;
- subgroup stability;
- concentration diagnostics;
- calibration for probabilistic outputs.

Important distinction:

- **data leakage**: future information enters features;
- **statistical dependence**: samples are correlated.

Both matter, but they require different remedies.

## 14. Survivorship and universe semantics

A current-company historical cohort is a valid research cohort but is not a survivorship-free universe.

Claims must match the selection contract.

Historical constituent membership, delistings, ticker changes and corporate reorganizations require explicit temporal handling before market-wide generalization claims.

## 15. Continuous learning

Continuous learning means continuous **measurement and candidate generation**, not uncontrolled continuous mutation.

Persist every prediction even when it does not trigger a trade.

A future production loop needs observation snapshot, prediction, realized outcome, calibration/error, drift diagnostics, candidate models, validation, promotion/rejection and rollback.

## 16. Current development order

Current evidence-driven order:

1. freeze/document current research checkpoint;
2. robustness of the existing H10 candidate signal;
3. stronger Market Brain Daily V003;
4. distributional Market Brain;
5. distributional Event Brain on the existing SEC corpus;
6. richer event semantics/expectations;
7. new information sources;
8. graph propagation;
9. trajectory/risk/decision layer;
10. controlled continuous learning.

See `docs/ROADMAP.md` for gates.

## 17. Criterion for progress

Do not measure progress only by simulated profit.

Before trading claims, demonstrate:

1. temporally coherent data;
2. reproducible baselines;
3. no obvious leakage;
4. proper OOS generalization;
5. calibrated distribution/probabilities;
6. incremental value from new information;
7. robustness across seeds/time/assets;
8. only then net return and drawdown after realistic costs.

The recurring question is:

> Does this component add reproducible out-of-sample information, or merely make the system fit historical noise more convincingly?
