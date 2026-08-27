# Research Decisions

This document records durable research choices and separates decisions made **before** results from interpretations made **after** results.

It is not a package changelog.

## D001 — Target object is a trajectory distribution

**Decision:** the long-term target is a distribution of future paths, not only terminal price or MFE.

\[
P(R_{t:t+T}\mid X_t,E_t,G_t,T)
\]

**Reason:** terminal return can hide economically important intermediate upside/downside and uncertainty.

**Status:** active.

## D002 — Horizon is learned, not extrapolated by a universal formula

Do not use fixed rules such as:

```text
mu_T = mu_1 * T
sigma_T = sigma_1 * sqrt(T)
```

as the system law.

Discrete horizons are acceptable research approximations.

**Status:** active.

## D003 — Market Brain must exist independently of news/events

The base model must forecast from market context alone.

Event Brain is evaluated for incremental information relative to that base.

**Status:** active.

## D004 — Documents are evidence, not events

Duplicate articles/filing resources do not become independent shocks.

Evidence must be clustered/normalized into stable event identities.

**Status:** implemented for SEC research path.

## D005 — Do not hardcode predictive economic meaning

Reliability, importance, direction, persistence and graph strength are learned/contextual quantities.

Deterministic rules may enforce identity, taxonomy, form selection and data integrity.

**Status:** active.

## D006 — `available_at` is the feature boundary

Prediction at `t` may use only information legitimately available by `t`.

Historical reconstruction must preserve the distinction between historical public-availability proxy and actual later retrieval.

**Status:** implemented; current deep SEC corpus is explicitly PIT=0.

## D007 — Stable event identities survive deeper rebuilds

Deep historical scaling must reuse economic identity when the underlying event is the same.

New observations/states use new feature versions when evidence completeness changes.

**Status:** implemented.

## D008 — Common scientific cohort window

For cross-sectional/sector market context, the event research cohort begins only when all required target assets have sufficient market-history readiness.

Current common start:

```text
2016-09-23
```

Old AAPL data remain stored but are excluded from the V003.1 scientific cohort before model-state construction.

**Status:** implemented.

## D009 — Form selection is an experiment boundary

Current deep SEC experiment uses:

```text
8-K, 8-K/A, 10-Q, 10-Q/A, 10-K, 10-K/A
```

Legacy Form 4 documents may remain in storage/clustering infrastructure but are not allowed to create V003.1 experiment events.

**Status:** implemented.

## D010 — Deep scaling test keeps model logic frozen

**Pre-result decision:** evaluate the same Event Brain V0.2 training architecture on the deep V003.1 corpus before changing models.

**Reason:** isolate the effect of data depth.

**Result:** H1/H3/H5 remain near zero/negative; H10 remains weakly positive.

**Status:** completed.

## D011 — H10 was the pre-specified candidate horizon

Before deep replication, H10 was identified as the only pilot horizon with a plausible incremental event signal.

The deep run tested H10 first.

**Result:** H10 control−contextual MAE delta `+0.02819 pp`, all four folds positive, bootstrap interval still crosses zero.

**Interpretation:** candidate survives weakly; no confirmation.

## D012 — Stop SEC scaling after V003.1 checkpoint

**Post-result decision:** current SEC corpus is large enough for the next scientific question.

Do not add more filings/assets merely to increase row count before testing robustness and improving Market Brain.

**Reason:** predictive bottleneck has shifted from event sample size to model/state quality and statistical robustness.

## D013 — Robustness objective is falsification

Event Brain V0.2.1 should try to **destroy** the H10 candidate.

Planned checks:

- simple constant baselines;
- train mean/median;
- always-up/down and train-majority direction;
- Ridge/ElasticNet/Huber;
- multiple RF seeds;
- filing/accession grouping;
- horizon-aware block bootstrap;
- early-OOS sensitivity;
- asset/type concentration;
- extreme-day sensitivity.

Do not tune this stage to maximize H10.

## D014 — Market Brain Daily is higher priority than richer Event Brain

**Post-result decision:** after robustness, improve the base market context.

Required direction includes strictly-as-of broad-market/sector/rate/volatility/regime features.

Target gate:

> Market Brain should consistently beat trivial zero/median baselines OOS before event complexity is treated as the main bottleneck.

## D015 — Move from scalar to distributional evaluation

The current Event Brain point-return MAE experiment is only a minimal information test.

Future research must evaluate whether events improve quantiles, probability forecasts, tail risk, path volatility and MFE/MAE.

Proper scoring rules should replace “tree dispersion = uncertainty”.

## D016 — Rich semantics come after distributional/base-model work

Do not add full text, expectations, guidance surprise, general news and graph propagation until the simpler base/distributional questions are understood.

This prevents new data sources from hiding weaknesses in the base model.

## D017 — Prediction remains separate from trading decision

Costs, broker settings, slippage, risk limits and position sizing belong downstream.

No current research metric should be described as a trade recommendation.

## D018 — Historical experiment artifacts are preserved

Do not delete old model/report/database experiment history simply because a newer version exists.

Documentation may be archived away from the root, but scientific lineage remains reproducible.


<!-- EVENT_T0_V001_START -->
## D020 — First public evidence, not SEC acceptance, defines event information t0

**Decision:** do not equate SEC filing acceptance with the first time the
market could have known an event.

Future multi-source normalization will distinguish:

```text
event_time
first_public_at
source published/accepted time
system observed/retrieved time
feature available_at
```

SEC remains the first authoritative event corpus and a high-quality anchor
source. Investor Relations, press-release wires, official channels, media,
calls/webcasts and macro authorities may reveal information earlier depending
on the event.

A later authoritative confirmation enriches the Event State from that point
forward; it does not retroactively rewrite the earlier information set.

**Reason:** event-return research is invalid if `t0` is placed after the
market had already received the information.

**Status:** active architecture contract.

## D021 — Market Brain Daily V003 is independent of event occurrence

**Decision:** train the daily Market Brain on all eligible asset-days at
session close, not only event-origin rows.

Event Brain integration will later use only the latest Market Brain
prediction/state whose market timestamp is no later than the event-state
timestamp.

**Reason:** the base model must estimate `P(Y|X,T)` independently before
testing the incremental information in `E`.

**Status:** Market Daily V003 foundation.
<!-- EVENT_T0_V001_END -->

## D019 — Documentation has canonical vs historical layers

Canonical docs describe current truth.

Package/fix READMEs are allowed but live under `docs/package-notes/` and are eventually archived.

No future ZIP should pollute repository root with competing `README_*` status documents.

**Status:** introduced by documentation restructure V001.
<!-- MARKET_V003_CORE_H1_FIX_V001 -->
## Market Daily V003 Core H1 label correction

The first Core audit was superseded because it allowed all H1 labels to be
`insufficient_future`.

Cause: H1 path volatility used sample std (`ddof=1`) on a one-return path.

Decision:

```text
path volatility := population std (ddof=0)
H1 path volatility := 0
```

The audit now treats missing/substantially unusable horizons as hard failures.
The processed Core DB is rebuilt from source observations rather than patched
in place.
<!-- MARKET_V003_RESULTS_V004_FACTORIZATION_V001 -->
## D022 — Factorize Market Brain before adding more context

Market V003 demonstrated that pooling own-asset, market-day and sector-day
signals into one asset-day nonlinear model is not currently robust.

Next architecture hypothesis:

```text
Market factor model
    unit = day
        +
Sector residual model
    unit = sector-day
        +
Asset residual model
    unit = asset-day
```

with exact target identity:

```text
asset return
= market factor
+ sector factor
+ asset residual
```

This is a hypothesis to test, not an established explanation of the V003
failure.

Before training V004:

1. quantify common-market and sector target variance;
2. quantify feature replication/topology by statistical unit;
3. freeze the factorized target contract;
4. materialize each level separately;
5. benchmark each component before recombination.

Do not yet add external market proxies, macro, Event Brain, distributional
outputs, or tune V003 after observing its benchmark.
<!-- MARKET_V004_MATH_FOUNDATION_V001 -->
## Decision — preserve V003 and expand Market Brain information carefully

V003 answered a deliberately narrow question: price/volume/relative state
alone, pooled at asset-day level, did not beat the preregistered absolute
return baseline.

This does not reject the project objective
`P(R[t:t+T] | X_t, E_t, G_t, T)`.

Decision:

1. retain all V003 artifacts/results as negative evidence;
2. test factorized mathematical targets without new external data;
3. if factorized components generalize, add external market-wide state
   incrementally to the statistically appropriate level;
4. require causal `available_at <= t` contracts for every enrichment;
5. do not tune V003 after observing its failure;
6. do not integrate Event Brain or distributional heads before a credible
   point-estimate Market Brain baseline exists.
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

## D023 — Scalar location failure does not veto distributional scale research

**Decision:** the V003–V005 scalar absolute-return failures remain valid negative evidence, but they do not require the project to postpone every distributional test until a point model beats the median.

**Reason:** conditional location and conditional dispersion are different statistical objects. A causal state variable may improve a proper distributional score and calibration even when it adds no median-return information.

This supersedes only the earlier sequencing clause that required a credible point-estimate Market Brain before distributional heads. It does not weaken the need for trivial baselines, purged walk-forward evaluation or preregistration.

**Status:** active after V006.

## D024 — V006 empirical volatility scaling is the required distributional control

**Decision:** retain the train-only empirical distribution rescaled by causal 20-session asset volatility as the first Distributional Market Brain control.

Evidence:

- positive origin-day-equal pinball delta at H1/H3/H5/H10;
- 5/10/20-day moving-block intervals exclude zero at all horizons;
- calibrated pooled central interval coverage;
- no improvement in median MAE or positive-return Brier.

Consequently, the supported increment is distribution scale. Any learned distributional model must show OOS incremental information beyond V006, not merely beyond an unconditional quantile baseline. V006 is not a production model and is not evidence of directional or tradable alpha.

**Status:** active.

## D025 — Identity conflict candidates cannot mutate canonical entities automatically

**Decision:** Identity Resolution Foundation V001 remains a review artifact.

The 28 conflict groups, 30 pairs and 3 row-quality candidates are inputs to upstream hygiene review. They do not authorize automatic merge, split, exclusion, jurisdiction writeback, canonical-entity creation or graph-edge promotion.

**Status:** active; graph promotion blocked pending review and rebuild.

## D026 — Product confidence and “psychology” have testable semantics

**Decision:**

- “investor psychology” is represented only through observable proxies or learned latent state with OOS evidence;
- market price/path is the observed outcome target; intrinsic value is latent and would require a separate explicit model;
- confidence shown to a user means calibrated probability/coverage at the requested horizon;
- scheduled future events may alter uncertainty without an assumed sign;
- a future path chart requires a coherent joint trajectory distribution, not independent horizon samples;
- automated improvement uses candidate evaluation, promotion and rollback rather than blind model mutation.

**Status:** canonical product interpretation.


<!-- MARKET_DIST_V0061_ROBUSTNESS_V001_START -->
## Decision — freeze V006.1 as falsification, not optimization

V006.1 preserves the completed V006 primary and asks where its conditional-dispersion claim does or does not hold. The experiment must reproduce frozen V006 daily OOS losses before any subgroup diagnostic is accepted. `asset_vol_5d_pct` and `asset_vol_63d_pct` are predeclared sensitivity scales only; neither can become the new primary from V006.1 results. The learned distributional model will receive its own version, preregistration and temporal selection design.
<!-- MARKET_DIST_V0061_ROBUSTNESS_V001_END -->
