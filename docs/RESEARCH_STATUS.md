# Research Status — 2026-08-27

**Status:** canonical empirical checkpoint  
**Scope:** research, not production trading

## Current checkpoint — 2026-08-27

### Completed falsification work

Event Brain V0.2.1 robustness did not convert the H10 scalar candidate into a general claim:

- Random Forest capacity-matched delta was positive for 4/5 preregistered seeds;
- mean seed delta: `+0.018895 pp`; median: `+0.028377 pp`;
- range: `-0.022576` to `+0.048059 pp`;
- simple linear families were negative;
- dependence/concentration checks were acceptable, but the early-OOS sensitivity was approximately null.

Interpretation: H10 may contain conditional nonlinear information, but it is seed- and period-sensitive. It is not confirmed event alpha and does not justify more SEC scaling.

Daily scalar Market Brain results are closed:

| Experiment | Primary result |
|---|---|
| V003 endogenous pooled HGB | worse than fold train median at H1/H3/H5/H10 |
| V004 factorized HGB | materially better than V003, still worse than train median at every horizon |
| V005.1 SPY/QQQ/IWM increment | incremental 10-day-block CIs cross zero at all horizons |
| V005.2 financial conditions increment | incremental 10-day-block CIs cross zero at all horizons; absolute skill remains negative |

No scalar daily model is promoted.
### Distributional V008.1 developmental pass and V009 prospective gate

V007's handcrafted adaptive asymmetric asset-scale model failed against the
raw vol63 empirical reference at H1/H3/H5/H10. No horizon had a positive point
estimate and calibration was worse at all horizons.

V008 then tested conditional standardized-return quantiles from the frozen
Core V003 endogenous state. The full candidate failed significantly against
the equally recent-calibrated vol63 reference at all four horizons:

| Horizon | Full minus calibrated-vol63 skill delta | Block-10 95% CI |
|---:|---:|---:|
| H1 | -0.005920 pp | [-0.009783, -0.001948] |
| H3 | -0.017368 pp | [-0.025117, -0.010092] |
| H5 | -0.025534 pp | [-0.039537, -0.012224] |
| H10 | -0.068362 pp | [-0.096638, -0.038895] |

The full candidate also lost to raw vol63 at all horizons. The 126-origin-day
recent recalibration itself harmed raw vol63 at H1/H3/H5/H10. The shallow
regularized profile was selected in all 20 nested fold/horizon selections.

Supported interpretation: the current full endogenous feature/representation/
model/calibration contract did not add stable distributional information
beyond raw vol63. This does not prove that every possible endogenous
representation is uninformative.

V008.1 completed and passed all six frozen H1 developmental checks:

- daily-equal pinball delta versus raw vol63: `+0.004703 pp`;
- block-10 95% interval: `[+0.002923, +0.006582]`;
- 4/5 temporal folds, 4/5 quantiles, 466/497 assets, 11/11 sectors and
  6/7 years positive;
- candidate calibration not worse;
- positive interval versus the mean five-seed capacity placebo and a positive
  point delta versus every placebo seed.

The improvement is concentrated in q05/q25/q75/q95; q50, median MAE and
positive-return Brier are worse. The supported developmental object is
conditional distribution shape/tails, not location, direction or alpha.
H3 corroborates the pattern; H5/H10 remain dependence-aware inconclusive.

V009 is now preregistered as the untouched temporal confirmation. It freezes a
single pre-holdout fit, the 497-asset snapshot cohort, a 16-hour seal window,
append-only predictions/outcomes and the first 252 consecutive eligible H1
origins as the only formal promotion cohort. The 126-origin checkpoint is
descriptive only. The plan passed and fit
`fit_e5c5616664c919a2624e6daaad39d1ca` is frozen over 1,078,329 rows through
target day 2026-08-24. No prediction was sealed for 2026-08-28: Yahoo's daily
chart row exposed complete Open/High/Low/Volume but null Close/Adj Close even
after the seal deadline. The quote metadata separately exposed a same-session
regularMarketPrice. Refresh V002 and additive migration 023 now permit only a
bounded same-session regularMarketPrice fallback, never post-market, and treat
the first quality-eligible observation as the initial PIT=0 reconstruction
while preserving failed retrievals and actual observation clocks. Five real
assets passed; 492 remain for the user-run source repair. The first sealed V009
batch is therefore pending and cannot precede 2026-08-31. The frozen fit and
historical selection through 2026-08-24 are unchanged.

### Temporal Dataset V001/V002 checkpoint — 2026-08-30

Temporal V001 completed its full configured-sparse materialization over the
frozen Core through 2026-08-24: 1,092,555 states, 497 assets, 17 taus and
18,573,435 outcome rows. All 4,370,220 H1/H3/H5/H10 rows match Core target day,
raw-close return, action overlap and status exactly; there are zero missing or
mismatched rows at absolute return tolerance `1e-9`. Source/Core were read-only,
V009 was not loaded and no model was trained.

The long-horizon selection gate is material and rejects raw-close exclusion as
the primary annual target. Corporate-action overlap among resolved origins is
26.25% at H21, 76.20% at H63, 79.09% at H126 and 80.32% at H252. At H252 the
median asset has 100% overlap; Materials/Energy exceed 99%, while Information
Technology/Communication Services are near 55%. A raw-close no-action model
would therefore learn a severely altered company/sector population.

Temporal V002 is the additive response. It preserves V001 as a control and
defines cash-inclusive total shareholder return as the product of
`(Close_t + cash_distribution_t) / Close_(t-1)` over `(origin,target]`.
Provider Close and cash values are already split-normalized, so recorded split
factors remain lineage and are not multiplied again. Provider Adjusted Close
is forbidden as target and used only to reconcile action timing/units under its
separate `Close_t/(Close_(t-1)-cash_t)` convention.

The full V002 sparse artifact is now complete: 18,573,435 outcomes over the
same 1,092,555 states and 17 taus. Full V001 parity and 4,101,105 no-action
H1/H3/H5/H10 identities have zero mismatches. Of 15,303 selected actions,
15,299 reconcile to the provider convention and four occur at grid start;
there are zero pending/failing action steps. All resolved outcomes are usable
at every tau and in every audited sector/year group. H252 recovers 777,057
action-overlap windows, 80.32% of its 967,432 resolved origins. The artifact
remains internally blocked and V009 is unchanged.

A separate economic-review/preregistration package is implemented. Its real
mechanical review passes target-distribution, support and arbitrary-tau prefix
gates. It flags 16 cash steps at or above 5% of previous close, five at or above
10%. Eleven can enter model-visible outcomes and require evidence-bound
entitlement review; five are pre-origin lineage only. No event is auto-approved. The
plan-only horizon-conditioned protocol uses 2,008 H252-resolved origin days
through 2025-08-21 for its common-support primary clock and leaves the following
252 days as per-tau recency diagnostics. Five purged folds are feasible; no
model is trained and the only current blocker is the 11-event special-action
decision file.

### Distributional Market Brain V006 empirical foundation

Preregistered versions:

- benchmark: `market_brain_distributional_v006_baseline_v001`;
- model: `market_brain_distributional_v006_empirical_baselines_v001`;
- market features: `market_daily_state_v003_core`;
- labels: `market_daily_reaction_v003_core`;
- dataset: `market_daily_v003_all_asset_days_current_cohort_research`;
- Core DB SHA-256: `2eccfe061b33bcd3fff6c244be972b379d9c4c3f1230532b5a66c72aaaf3be19`;
- five purged expanding folds; 30% initial history;
- primary unit: equal-weight origin trading day;
- uncertainty: 3,000 moving-block bootstrap repetitions at 5/10/20 origin days.

Frozen comparison:

```text
baseline(q)  = Q_train(return_pct, q)
candidate(q) = median_train
               + Q_train((return_pct - median_train) / asset_vol_20d_pct, q)
               * asset_vol_20d_pct_at_prediction
```

Only the causal `asset_vol_20d_pct` state is model-visible. All distributional shapes and probabilities are fit from training outcomes only. Rows with nonpositive scale use the unconditional training distribution. No event, graph, macro, external proxy or broker-cost feature enters the model.

Primary OOS result:

| Horizon | OOS rows | OOS origin days | Daily-equal pinball delta | 95% CI, block 10 |
|---:|---:|---:|---:|---:|
| H1 | 763,935 | 1,582 | +0.009198 pp | [+0.006543, +0.011498] |
| H3 | 743,503 | 1,580 | +0.013490 pp | [+0.008329, +0.018274] |
| H5 | 723,573 | 1,579 | +0.013420 pp | [+0.007085, +0.019961] |
| H10 | 673,391 | 1,575 | +0.012663 pp | [+0.002568, +0.024809] |

Positive means lower candidate pinball loss. Every horizon is positive under all preregistered 5/10/20 block lengths. H1/H3/H5 are positive in all five temporal folds; H10 is positive in 4/5 and slightly negative in the earliest fold.

Pooled candidate coverage:

| Horizon | Central 50% | Central 90% |
|---:|---:|---:|
| H1 | 0.5019 | 0.9051 |
| H3 | 0.4998 | 0.9052 |
| H5 | 0.4989 | 0.9074 |
| H10 | 0.5000 | 0.9102 |

Critical negative controls:

- median MAE is effectively unchanged;
- positive-return Brier score is slightly worse at every horizon;
- this is a terminal-return distribution, not a coherent path distribution;
- historical reconstruction is not strict PIT;
- the current-company cohort is not survivorship-free.

Supported claim:

> Causal 20-session asset volatility contains reproducible OOS information about conditional return-distribution scale relative to an unconditional train empirical distribution.

Unsupported claims: directional prediction, expected-return alpha, event alpha, trajectory prediction, profitability after costs, production readiness.

### Event–Graph identity checkpoint

Identity Resolution Foundation V001 built 28 conflict groups, 30 review pairs and 3 row-quality candidates. Audit status is `REVIEW`, not `PASS` for promotion:

- 10 reference-equivalent candidates;
- 11 temporal rejurisdiction/reporting-change candidates;
- 8 same-accession distinct/source-error candidates;
- 1 hierarchy/granularity candidate;
- zero automatic merges, splits, verdicts, exclusions, canonical entities or graph edges;
- no main-database mutation.

The next identity gate is human/scientific review followed by an upstream Structured Rows V002 hygiene patch and Registry V2 rebuild. Predictive graph propagation remains blocked.

### Ordered next work

Data-preparation correction (2026-08-28): the full close-aligned Event Dataset
V001 run completed but its clock contract is rejected. HTTP Last-Modified
metadata incorrectly shifted 169 eligible states by more than one day (maximum
375.329 days). Its old integrity PASS repeated the faulty rule; V001 is
preserved as failure evidence and must not be trained on.

V002 separates verified HTTP metadata from SEC availability, retains real
revision exclusions and adds independent temporal/coverage/quarantine checks.
The complete 2,001-state run now passes persisted/replay integrity with zero
unexplained clock shifts. It admits 1,885 states, quarantines 115 cross-accession
states plus one same-file multi-reference AAPL state, and produces 4,086
scenario rows. Exactly 151 otherwise eligible early states lack an exact Market
Core state (all 40 from 2016 and 111 from 2017); they remain excluded rather
than retimed. The selected base cohort begins 2017-08-30.

The V002 review is closed as data-ready for preregistration, not training.
Cross-accession rows stay out of the primary dataset; the single AAPL case is
a benign duplicate file-version reference but remains excluded to preserve the
immutable V002 contract. H10 has approximately 24.3% corporate-action exclusion
under the zero-delay scenario and cannot be selected silently as the primary.
No model was fit and no predictive result follows. V009 remains independent.
Details: [DISTRIBUTIONAL_EVENT_DATASET_V002.md](DISTRIBUTIONAL_EVENT_DATASET_V002.md).

1. Materialize each eligible session through the audited daily source/Core
   refresh, preserving the frozen V009 training hash.
2. Seal each eligible H1 origin before the 16-hour deadline and later attach
   its outcome through the append-only registry; never refit during confirmation.
3. Evaluate the fixed first-126 descriptive and first-252 confirmatory cohorts.
4. Build Distributional Event Brain on the existing SEC corpus in parallel as
   a separate developmental comparison against the frozen Market Brain.
5. Complete upstream identity hygiene; graph prediction remains blocked until
   direct event information adds OOS value.
6. Coherent paths, graph propagation, risk, costs and decisions remain gated.

## Historical and version-specific detail

The current checkpoint above supersedes earlier "current"/"next" statements
retained below as historical context. Do not execute old stage instructions
from this appendix. For today's persisted counts and versions, use the
read-only auditor described in [CONTEXT_RECOVERY.md](CONTEXT_RECOVERY.md);
this dated scientific record is not a live database dashboard.

## 1. Executive summary

The project has completed the first serious deep SEC Event Brain research corpus.

The data/lineage infrastructure is now substantially stronger than the predictive models.

Current high-level conclusion:

> The supported daily result is conditional distribution scale and a small
> developmental own-state shape/tail increment, not location or tradable alpha.
> V008.1 passed its frozen historical falsification; V009 is the untouched
> prospective gate required before the Market Brain can be promoted.

## 2. Market data / Market Brain

### Intraday Market V002

The previously frozen intraday V002 baseline improved the 60-minute pilot relative to V001:

- paired rows: 131,361;
- V001 MAE: `0.466421849`;
- V002 MAE: `0.448511859`;
- relative MAE improvement: about `3.84%`;
- directional accuracy: about `0.5270 → 0.5653`;
- paired bootstrap MAE delta: about `+0.01781`;
- 95% interval: about `[+0.00868, +0.02854]`.

The 5-minute gain was much weaker and directional uncertainty crossed zero.

Important limitation: this intraday validation covered only about seven sessions and is not production evidence.

### Daily market context used by Event Brain

Current daily market features include asset returns 1/3/5/10/20, volatility 5/20, range, distance features, volume ratio, leave-one-out cross-section and leave-one-out sector context.

It does not yet contain a mature broad-market/macro/regime representation.

Deep Event benchmark trivial-zero vs Market MAE:

| Horizon | Zero MAE | Market MAE | Market vs zero |
|---:|---:|---:|---|
| 1 | 1.8725 | 1.9482 | worse |
| 3 | 2.5308 | 2.5379 | roughly equal / worse |
| 5 | 3.2173 | 3.1772 | slightly better |
| 10 | 4.2185 | 4.3244 | worse |

Conclusion: **Market Brain Daily is the biggest predictive weakness.**

## 3. Deep SEC corpus V003.1

Research cohort:

```text
AAPL MSFT JPM BAC XOM CVX LLY JNJ WMT COST
```

Scientific common window:

```text
2016-09-23 → 2026-08-24
```

Persisted scale:

- 1,704 cohort-eligible filings;
- 10,642 persisted evidence semantics / cohort raw documents;
- 1,939 unique normalized events;
- 305 reused stable event identities;
- 1,634 new event identities;
- 2,001 Event States;
- 10 assets;
- 0 strict-PIT states/observations in the reconstructed historical corpus.

The corpus explicitly remains a **historical research reconstruction**.

## 4. Event State distribution

States are broadly distributed rather than concentrated only in recent history:

| Year | States |
|---:|---:|
| 2016 | 40 |
| 2017 | 172 |
| 2018 | 157 |
| 2019 | 209 |
| 2020 | 221 |
| 2021 | 221 |
| 2022 | 220 |
| 2023 | 204 |
| 2024 | 209 |
| 2025 | 208 |
| 2026 | 140 |

JPM event coverage begins in 2019 under the current metadata-depth cap; the market price context for the cohort still begins at the common window.

Largest event categories:

- financial results disclosure: 409;
- other material disclosure: 403;
- quarterly report disclosure: 292;
- Regulation FD disclosure: 246;
- management/board change: 234.

## 5. Reaction labels V003.1

2,001 states × 4 horizons = 8,004 labels.

Status totals:

- usable: 6,343;
- corporate-action overlap: 495;
- intraday/daily-resolution mismatch: 1,164;
- insufficient future sessions: 2.

Usable label counts:

| Horizon | Usable labels |
|---:|---:|
| 1 | 1,701 |
| 3 | 1,668 |
| 5 | 1,620 |
| 10 | 1,354 |

The model-ready loader has one fewer row at each horizon due to its complete-feature contract.

## 6. Deep model-ready datasets

| Horizon | Rows | Unique events | Unique origin days | Event types |
|---:|---:|---:|---:|---:|
| 1 | 1,700 | 1,650 | 974 | 21 |
| 3 | 1,667 | 1,620 | 957 | 21 |
| 5 | 1,619 | 1,573 | 932 | 21 |
| 10 | 1,353 | 1,314 | 849 | 19 |

No sector fallback rows are present in these datasets.

## 7. Event Brain V0.2 pilot

The small recent pilot produced no convincing incremental event evidence at 1/3/5 sessions.

At H10 the capacity-controlled comparison was approximately:

- MAE delta baseline−contextual: `+0.062 pp`;
- bootstrap 95% interval roughly `[-0.006, +0.133]`;
- win rate roughly `57%`;
- only 2 of 4 folds positive.

This was treated as a preliminary hypothesis, not a conclusion.

## 8. Deep replication — same V0.2 training logic on V003.1

The deep runner explicitly fixes:

```text
event_feature_version  = event_state_v0031_deep
label_version          = event_reaction_daily_v0031_deep
market_feature_version = daily_asset_cross_section_sector_v002_leave_one_out
```

Primary incremental comparison:

```text
capacity-control residual
vs
contextual residual (market + event features)
```

Results:

| Horizon | MAE delta control−contextual | Interpretation |
|---:|---:|---|
| 1 | +0.00427 pp | effectively zero |
| 3 | −0.00377 pp | effectively zero / slightly worse |
| 5 | −0.01359 pp | slightly worse |
| 10 | **+0.02819 pp** | weak positive candidate |

H10:

- pooled OOS rows: 778;
- MAE control: `4.76328%`;
- MAE contextual: `4.73509%`;
- relative MAE reduction vs capacity control: about `0.59%`;
- paired bootstrap 95% interval: `[-0.00366, +0.05968]`;
- candidate absolute-error win rate: `0.5064`;
- directional accuracy delta: essentially zero;
- **all 4 H10 folds have lower contextual MAE than capacity-control MAE**.

Interpretation:

> H10 became smaller in magnitude than the pilot but more temporally consistent. The interval still crosses zero, so the effect is not confirmed.

## 9. Important negative result

The simple `market + event` model is worse than `market` at all tested horizons.

At H10:

- Market MAE: `4.32442%`;
- Market+Event MAE: `4.57305%`;
- paired MAE delta: `−0.24862 pp`;
- bootstrap interval remains negative.

Therefore the current event representation cannot simply be added to the market predictor and assumed useful.

## 10. Why the current Event Brain test is incomplete

The current target is primarily `return_pct`.

But the architecture expects events to potentially affect uncertainty, downside/upside tails, path volatility, MFE, MAE and regime probability.

The label system already stores `mfe_pct`, `mae_pct` and `realized_path_vol_pct`; these are not yet modeled.

Therefore:

> “event features do not improve point-return MAE at H1/H3/H5” does **not** imply that events contain no useful distributional information.

## 11. Known scientific limitations

- Current 10-company cohort is not survivorship-free.
- Historical SEC evidence is reconstructed and PIT=0.
- OOS folds begin around 2021 because earlier history forms training/warmup.
- Multiple events can come from the same accession/filing.
- Multiple states can represent one event as evidence evolves.
- Multi-session targets overlap in calendar time.
- Current bootstrap resamples origin days, not horizon-aware multi-day blocks.
- Sector context is thin.
- Daily Market Brain is weak.
- Event representation is taxonomic/structural, not full semantic surprise.
- Expectations/consensus/guidance novelty are absent.
- Corporate-action exclusions remove a meaningful fraction of long-horizon labels.
- No production transaction-cost/risk validation exists.

## 12. Current research claim

Allowed:

> In the current 10-company historical reconstruction, factual SEC event-state features show a weak candidate incremental MAE improvement at approximately 10 sessions relative to a capacity-matched residual control. The effect is positive across four folds but its bootstrap interval still crosses zero.

Not allowed:

- “Event Brain is profitable.”
- “SEC events predict stock direction.”
- “The effect is statistically proven.”
- “The result generalizes to the US equity market.”
- “The historical data are strict PIT.”
- “The current model outputs a calibrated future distribution.”

## 13. Current next step

No more SEC scaling.

Next:

1. Event Brain V0.2.1 robustness/falsification.
2. Market Brain Daily V003.
3. Distributional modeling.

See `ROADMAP.md`.
<!-- MARKET_V003_BROAD_BACKFILL_V001 -->
## Market Daily V003 foundation audit — 2026-08-25

Foundation audit result:

```text
active equities                       503
assets with quality-gated daily data   10
assets >= 1,260 daily sessions         10
assets >= 2,000 daily sessions         10
latest assets ready for 253-day state  10
strict historical PIT rows              0
```

No day currently reaches the minimum 50-asset cross-section gate.

Decision:

```text
BROAD_PANEL_BACKFILL_REQUIRED
```

The next data step is a listing-aware daily Yahoo backfill for the existing
503-equity current research cohort. Assets enter the model dynamically after
sufficient own history; they are not forced into a common historical start.

This cohort is explicitly **not survivorship-free historical index
membership**. It is a current-asset historical research cohort.

SPY/QQQ/IWM, sector ETFs, volatility/rate/credit proxies are absent and are
deferred until after the core broad-equity panel is audited.

Legacy macro observations remain excluded because no causal
release/vintage/availability contract exists.
<!-- MARKET_V003_CORE_DATASET_V001 -->
## Market Daily V003 broad backfill closed

Broad current-cohort backfill result:

```text
493 planned backfills
490 completed
3 quality-quarantined: FISV, HUBB, MNST
500 assets with quality-gated daily data
497 assets ready for >=253-session state
489 assets with >=1260 daily sessions
```

The three failures each downloaded the requested history but failed a strict
single-row quality condition. They remain quarantined rather than weakening
the gate. The >=300 / >=300 broad-panel readiness gate is passed.

Next scientific stage is the deterministic Market Daily V003 Core Dataset:
all eligible asset-days + own state + leave-one-out market/sector context +
separate 1/3/5/10-session labels. External proxies, macro and event features
remain deferred.
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
<!-- MARKET_V003_BENCHMARK_V001 -->
## Market Daily V003 Core gate passed

Corrected Core audit:

```text
status PASS
1,092,555 states
497 assets
1,078,329 usable H1 labels
1,049,926 usable H3 labels
1,021,619 usable H5 labels
951,231 usable H10 labels
```

Corporate-action overlap fraction:

```text
H1   1.26%
H3   3.77%
H5   6.27%
H10 12.48%
```

The raw-close label contract is retained for the preregistered V003 benchmark;
it is interpreted as a no-corporate-action-overlap research target, not a
production total-return target.

Next: frozen Market Daily V003 Benchmark V001.
<!-- MARKET_V003_BENCHMARK_V0011 -->
## Active Market Daily V003 benchmark

Active preregistration: `market_brain_daily_v003_benchmark_v0011`.

V001 was superseded before any model performance was observed. The Core
dataset, horizons, 30% initial training fraction, five-fold purged
walk-forward, primary model and primary MAE comparison are unchanged.

The change only strengthens baselines, makes HGB stopping behavior explicit,
exposes purge boundaries, and freezes code/data/environment hashes.
<!-- MARKET_V003_RESULTS_V004_FACTORIZATION_V001 -->
## Market Daily V003 Benchmark V001.1 — closed

The preregistered primary claim was rejected at every horizon.

Primary metric is:

```text
train_median MAE - HGB_full MAE
```

so positive is better for HGB.

Observed pooled deltas:

```text
H1   -0.0484 pp
H3   -0.2945 pp
H5   -0.7561 pp
H10  -1.0025 pp
```

All 5/10/20-origin-day moving-block bootstrap confidence intervals remain
strictly below zero at every horizon.

The dominant nonlinear degradation is the transition:

```text
HGB own -> HGB own + cross-section
```

which is negative at H1/H3/H5/H10. Sector context is smaller and mixed but
does not rescue the full model.

Therefore Market V003 is not promoted to distributional modeling and is not
used as the base for Event Brain integration.
<!-- MARKET_V004_MATH_FOUNDATION_V001 -->
## Market Brain Daily V004 mathematical foundation

V003 remains a canonical negative benchmark. It is not deleted or rewritten.

The next Market Brain candidate remains inside Architecture Phase C. V004 tests
whether a hierarchical/factorized target representation improves temporal
generalization before adding external information or distributional outputs.

V004 separates statistical units:

```text
market: one row per origin day
sector: one row per sector-day
asset:  one row per asset-day
```

It materializes two target decompositions:

```text
additive:
R_i = M + S + E_i

dynamic factor:
R_i = beta_i,t * M + gamma_i,t * S + alpha_i
```

`beta` and `gamma` are estimated only from observations available through the
origin close. Neither factorization is assumed correct until walk-forward
evaluation supports it.

After the mathematical factorization gate, external Market State information
will be added incrementally: market ETFs, sector ETFs, volatility,
rates/credit, then vintage-causal macro. Events remain deferred until the base
Market Brain shows skill.
<!-- MARKET_V004_FACTORIZED_BENCHMARK_V001 -->
## Market Brain Daily V004 factorized benchmark — preregistration

The V004 mathematical dataset passed its identity/coverage audit. No
predictability claim follows from that audit.

Primary experiment:

```text
predict market factor once/day
+ predict sector residual once/sector-day
+ predict asset residual once/asset-day
= reconstructed absolute asset return
```

Primary candidate: fixed HGB additive reconstruction.
Primary baseline: the exact V003 fold-specific train median prediction on the
same OOS state rows.

Secondary references:

```text
V003 HGB full
Ridge factorized reconstruction
dynamic beta/gamma reconstruction
```

Dynamic beta is secondary because its ~85.7% coverage creates a restricted
comparison subset.

The outer test boundaries are inherited exactly from Benchmark V001.1.
Every component training row must have `target_end_day < first_test_day`.

No proxy data, macro, events, distributional heads or post-result tuning enter
this benchmark.
<!-- MARKET_V004_FACTORIZED_V0011_COVERAGE_FIX -->
## Market V004 factorized benchmark V001.1 coverage correction

V001 was stopped at the plan gate before model results.

The plan revealed that the additive primary was accidentally restricted to
rows with finite dynamic beta/gamma features (~85.7% coverage). That violated
the preregistered contract: additive is the broad-coverage primary, dynamic
beta/gamma is secondary.

V001.1 separates additive and dynamic feature availability and hard-fails its
plan unless additive coverage includes all V003 OOS state rows and at least
98% of usable V004 target rows.

No V001 factorized model performance was observed before this correction.
<!-- MARKET_V004_FACTORIZED_V0012_DUPLICATE_EXPOSURE_FIX -->
## Market V004 factorized benchmark V001.2 duplicate-exposure correction

V001.1 also stopped at the plan gate before model results.

Root cause of persistent ~85.7% additive coverage: beta/gamma exposures existed
both in `v004_factor_targets` and `v004_asset_states`. The modeling merge kept
the state copies with `_state` suffixes. Those aliases were not excluded by
the V001.1 dynamic-feature filter, so they still gated the additive primary.

V001.2 makes `v004_asset_states` the canonical source of exposure features,
drops exposure copies from the target table before the merge, and hard-fails
if suffixed exposure aliases survive.

The plan additionally requires:

```text
additive_asset_rows == raw_usable_asset_rows
all V003 OOS states included
20 additive asset features
25 dynamic asset features
dynamic subset strictly smaller than additive
```

No V004 factorized model performance was observed before this correction.
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
<!-- MARKET_V005_MARKET_TRADABLES_BENCHMARK_V001 -->
## Market Brain Daily V005 market-tradables benchmark — preregistration

V004 factorization remains the frozen control. V005 tests one information
increment only: SPY/QQQ/IWM market state available at the origin-session close.

Primary comparison:

```text
V004 additive HGB reconstruction
vs
V005 additive HGB reconstruction
```

Only the market-level model receives new features. Sector and asset models,
targets, folds, hyperparameters and OOS state rows are unchanged.

The new market block contains 22 features derived from SPY/QQQ/IWM.
Historical Yahoo reference data uses the documented historical-session-close
assumption and is not strict provider point-in-time replay.

Two conclusions are kept separate:

1. incremental information value: V005 vs V004;
2. absolute skill checkpoint: V005 vs fold train median.

An external block can be retained for the next information stage if its paired
moving-block bootstrap improvement over V004 is positive across the
preregistered 5/10/20 origin-day blocks. This does not by itself imply absolute
market-prediction skill.

No sector ETFs, VIX, rates/credit, macro, events, distributional heads or
hyperparameter search enter this benchmark.
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
## V006.1 robustness/falsification — preregistered

The completed V006 conditional-dispersion result remains unchanged. The next active experiment is a diagnostics-only attempt to falsify or narrow that claim by exact source reproduction, tail analysis, asset/sector concentration, train-defined volatility regimes, calibration drift, direct comparison with the `asset_empirical` secondary reference and predeclared `vol5`/`vol63` scale sensitivities.

No V006.1 diagnostic may be used to retroactively select a replacement primary specification. A learned distributional Market Brain remains blocked until V006.1 is interpreted.
<!-- MARKET_DIST_V0061_ROBUSTNESS_V001_END -->

<!-- MARKET_DIST_V007_ADAPTIVE_TAIL_V001_START -->
## Distributional V006.1 closed; V007 adaptive-tail model active

V006.1 reproduced the completed V006 result on all H1/H3/H5/H10 samples and did not find asset/sector concentration capable of removing the positive V006-vs-global result. It also narrowed the claim: V006 is directionally asymmetric, has regime-dependent calibration error, and a 63-session volatility sensitivity is stronger than vol20 at H3/H5/H10. The global point result is therefore real enough to escalate, but the V006 linear symmetric scale formula is not treated as a final model.

Active next experiment: `market_brain_distributional_v007_adaptive_tail_v001`. It is a low-dimensional learned/semi-parametric distributional model with nested temporal selection, asset-specific structural tail anchors, separate downside/upside dynamic scales, and no learned location. `vol63_scaled_empirical` is the new preregistered primary reference; V006, asset empirical and global empirical remain controls.

V007 is developmental evidence because its hypothesis was informed by V006.1 outcomes. It is not independent prospective confirmation, not a path model and not production-ready.
<!-- MARKET_DIST_V007_ADAPTIVE_TAIL_V001_END -->

<!-- MARKET_DIST_V007_ZERO_VOL_AMENDMENT_V0011_START -->
## V007 pre-performance implementation amendment — exact zero volatility

The first V007 H1 benchmark attempt aborted during data loading before any OOS metric was produced. Core V003 permits exact zero rolling volatility, while the initial V007 loader incorrectly required both vol20 and vol63 to be strictly positive.

V007.0.1 corrects only this domain handling. Exact observed zero volatility is mapped to the already-frozen lower log-ratio clip; a nonpositive per-asset TRAIN median normalizer falls back to the positive global TRAIN median. The vol20 control restores the completed V006 `global_empirical_fallback` behavior for nonpositive scale rows, and vol63 uses the same prospective control rule. Negative/null volatility remains a hard error.

No rows are dropped, no epsilon is introduced, and no alpha/lambda/kappa grid, feature, primary reference, quantile, horizon, fold or score is changed. The plan gate now reports zero/negative/null scale support from the real Core V003 DB. This amendment must be committed before rerunning V007.
<!-- MARKET_DIST_V007_ZERO_VOL_AMENDMENT_V0011_END -->

<!-- MARKET_DISTRIBUTIONAL_V008_V001_START -->
## Distributional Market Brain V007 close / V008 preregistration — 2026-08-27

V007 Adaptive Asymmetric Asset Scale is closed as a negative developmental result:
- all H1/H3/H5/H10 horizon gates failed;
- zero horizons had a positive point estimate versus the `vol63_scaled_empirical` primary reference;
- the candidate remained better than the unconditional empirical distribution but did not add information beyond the stronger vol63 reference;
- calibration was worse than vol63 at all four horizons.

Interpretation: conditional dispersion information is real, but hand-parameterizing asset anchors plus vol20/vol63 asymmetric scaling did not add reproducible information beyond the strong long-memory empirical scale baseline.

V008 tests a different question. Terminal return is standardized by causal `asset_vol_63d_pct`, and shallow/medium regularized HGB quantile learners predict the remaining conditional residual distribution from the frozen endogenous Core V003 Market State. The primary reference is a `vol63` empirical distribution given the same 126-origin-day train-only standardized quantile recalibration as the candidate. Scale-only and own-state learners are diagnostics and cannot rescue a failed full-endogenous primary after results.

If V008 fails, the next research action is information enrichment, not additional endogenous model capacity.
<!-- MARKET_DISTRIBUTIONAL_V008_V001_END -->

<!-- MARKET_V008_SPLIT_FEASIBILITY_V0011 -->
### Market Distributional V008 v0011 — pre-performance split-feasibility amendment

The original V008 v001 benchmark aborted before any model fit or OOS performance metric because the earliest 30% outer fold could not simultaneously satisfy 126 recent calibration origin days, 126 minimum nested validation origin days, and 500 minimum nested training origin days after purging. No V008 performance was observed.

V008 v0011 preserves the frozen scientific question, features, H1/H3/H5/H10, five 30%-initial purged expanding outer folds, 126-day recent calibration window, 126-day minimum inner validation, HGB profile set, vol63_recent_calibrated primary reference, metrics, bootstrap and gates. The only scientific-control change is `minimum_inner_train_origin_days: 500 -> 378` (1.5 trading years) for nested profile selection. Final fold models remain fit on the full development block. The plan now performs a clock-only conservative split-feasibility audit before benchmarking.

<!-- EXPECTATION_CAPTURE_FOUNDATION_V001_START -->
## Expectation / Information Capture Foundation V001 — parallel data asset

While Market Distributional V009 remains frozen in prospective holdout, a separate append-only information-capture database may accumulate strict-PIT observations of scheduled events, expectations/guidance and later reported economic facts. This foundation is **not model-visible**, does not modify V009, and makes no predictive claim. Historical backfills remain `strict_pit=0`; only genuinely observed live evidence may be `strict_pit=1` under the capture contract.
<!-- EXPECTATION_CAPTURE_FOUNDATION_V001_END -->

<!-- TEMPORAL_DISTRIBUTIONAL_RUNNER_V001_START -->
## Temporal total-return distribution — runner ready, development not yet run

Market Temporal V002 remains immutable at 18,573,435 sparse outcomes. All 11
model-visible material cash events now have primary-source, SHA-bound decisions;
economic review is `PASS`. The 80-outcome extreme-tail lineage audit and the
external selection-mask audit pass; the resulting mask contains zero exclusions.

The shared `Q_q(total_return | own state,tau)` runner is implemented, tested and
real-preflighted. It supports every integer tau 1..252, fits on three balanced
deterministic anchors per origin, evaluates all 12 development anchors, and
keeps H7/H17/H42/H90/H180 sealed. The real preflight selected 3,277,665
origin-anchor rows before rowwise purge with max/min anchor ratio 1.00413.

Current next action: execute five resumable development folds, aggregate the
predeclared gate, and stop if it does not pass. Only an exact development PASS
may create the SHA freeze and open holdouts once. V009 remains isolated.
<!-- TEMPORAL_DISTRIBUTIONAL_RUNNER_V001_END -->
