# Experiment Registry

This file summarizes major scientific experiments. Detailed machine-readable outputs remain under `reports/` and `data/processed/`.

Do not overwrite historical report directories when introducing a new experiment version.

Preregistrations are retained as written; later completed interpretations for
the same experiment supersede their pre-result status, not their frozen design.
Current scientific status is in RESEARCH_STATUS.md. For report existence,
hashes and persisted-data checks, see [CONTEXT_RECOVERY.md](CONTEXT_RECOVERY.md).
The context auditor does not reproduce experiments or make promotion decisions.

## E-MARKET-V001/V002 — Intraday Market Brain

**Purpose:** improve market-only intraday prediction without news.

### 60-minute paired comparison

- paired rows: 131,361;
- V001 MAE: `0.466421849`;
- V002 MAE: `0.448511859`;
- relative MAE improvement: `~3.84%`;
- directional accuracy: `~0.527 → ~0.565`;
- bootstrap MAE delta: `~+0.01781`;
- 95% CI: `~[+0.00868, +0.02854]`.

**Interpretation:** meaningful pilot improvement, but underlying historical coverage was only about seven sessions.

### 5-minute

Smaller MAE improvement; directional CI crossed zero.

**Status:** V002 frozen as experimental intraday baseline.

## E-EVENT-V001 — First daily Event Brain

**Purpose:** prove the end-to-end event dataset/training path.

**Status:** superseded by V002 methodology.

See historical archive/package notes for implementation detail.

## E-EVENT-V002-PILOT — Capacity-controlled event benchmark

**Data:** recent 10-company pilot, roughly 2024–2026.

**Method:**

- 4-fold purged event-grouped walk-forward;
- RF residual architecture;
- capacity control = residual using Market features;
- contextual candidate = residual using Market + Event features;
- paired bootstrap by origin day.

**Primary H10 result:**

- control−contextual MAE delta: about `+0.062 pp`;
- 95% CI crossed zero;
- win rate about `57%`;
- 2/4 folds positive.

**Interpretation:** weak preliminary H10 candidate; no convincing H1/H3/H5 signal.

## E-DATA-V0031 — Deep SEC Event Corpus

**Scientific window:** `2016-09-23 → 2026-08-24`.

Scale:

- 1,704 eligible filings;
- 10,642 persisted evidence semantics;
- 1,939 normalized event identities;
- 2,001 Event States;
- 8,004 total reaction labels;
- 6,343 usable labels.

Versions:

```text
sec_event_normalizer_v0031_deep_raw_lineage
event_state_v0031_deep
event_reaction_daily_v0031_deep
```

Historical reconstruction is explicitly PIT=0.

**Status:** completed / frozen for next research phase.

## E-EVENT-V0031-DEEP — Same V0.2 model logic, deeper data

**Purpose:** test whether the pilot effect survives ~10 years of data without changing model logic.

Dataset rows:

```text
H1  1700
H3  1667
H5  1619
H10 1353
```

Primary incremental result:

| H | control−contextual MAE delta |
|---:|---:|
| 1 | +0.00427 pp |
| 3 | −0.00377 pp |
| 5 | −0.01359 pp |
| 10 | **+0.02819 pp** |

H10 paired bootstrap:

- 95% CI: `[-0.00366, +0.05968]`;
- absolute-error win rate: `0.5064`;
- all four OOS folds lower contextual MAE than capacity-control MAE.

Simple `market + event` is worse than Market alone.

**Interpretation:** H10 remains a weak candidate but is not statistically confirmed.

Reports:

```text
reports/event_brain_v0031_deep/
```

**Status:** completed.

## E-EVENT-V0021-ROBUSTNESS — preregistration and result

Predeclare before execution:

- baseline suite;
- simple model suite;
- RF seed set;
- accession/filing grouping sensitivity;
- block bootstrap definition;
- early-OOS sensitivity;
- subgroup/concentration diagnostics.

**Objective:** falsify H10, not optimize it.

No changes to event corpus during this experiment.

**Status:** completed; candidate narrowed, not promoted.

- positive RF seeds: 4/5;
- mean capacity-matched delta: `+0.018895 pp`;
- median delta: `+0.028377 pp`;
- seed range: `[-0.022576, +0.048059] pp`;
- simple linear families: negative;
- early-OOS sensitivity: approximately null.

<!-- MARKET_V003_BENCHMARK_V001 -->
## Market Brain Daily V003 Benchmark V001 — preregistration

Primary comparison:

```text
train median vs HGB full market state
```

Five purged expanding temporal folds, initial 30% training history. Training
rows satisfy:

```text
target_trading_day < first_test_origin_day
```

Models are fixed before results:

```text
zero
train mean
train median
asset train mean
same-horizon momentum

Ridge full
SGD Huber full

HistGradientBoosting own-only
HistGradientBoosting own + cross-section
HistGradientBoosting full (+ sector)
```

No hyperparameter tuning and no best-model selection for the primary claim.

Inference uses paired daily losses and moving-block bootstrap on origin days
(5/10/20). Row-level iid confidence intervals are not used.

Random Forest is intentionally deferred to robustness because the broad panel
contains roughly one million rows per horizon; it is not needed to establish
the first nonlinear benchmark.
<!-- MARKET_V003_BENCHMARK_V0011 -->
## Market Brain Daily V003 Benchmark V001.1 — supersedes V001 before results

No V001 model performance was observed before this hardening.

Primary scientific design is unchanged:

```text
all eligible asset-days
H1 / H3 / H5 / H10
5 purged expanding temporal folds
initial_fraction = 0.30
primary = train median vs HGB full
moving-block bootstrap by origin day
```

Pre-result hardening:

```text
+ asset train median baseline
+ always-up/down/train-majority direction baselines
+ HGB early_stopping=False explicitly
+ visible latest train target / purge row counts per fold
+ git/environment/code/Core-DB SHA256 preregistration
```

V001 reports remain historical and V001.1 writes to `benchmark_v0011/`.
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

## E-MARKET-V003–V0052 — scalar daily sequence result

**Status:** completed; no model promoted.

Origin-day-equal MAE deltas use baseline minus candidate, so positive is better. The table reports the preregistered 10-origin-day moving-block point estimate.

| Horizon | V003 vs median | V004 vs median | V005.1 vs V004 | V005.2 vs V004 |
|---:|---:|---:|---:|---:|
| H1 | -0.04857 pp | -0.00823 pp | -0.00085 pp | +0.00231 pp |
| H3 | -0.29275 pp | -0.01708 pp | -0.00518 pp | +0.00573 pp |
| H5 | -0.74796 pp | -0.03348 pp | +0.00006 pp | +0.00233 pp |
| H10 | -1.00287 pp | -0.08026 pp | +0.01496 pp | -0.00122 pp |

V003 and V004 are significantly worse than the train median at every horizon under the preregistered blocks. V005.1 and V005.2 incremental confidence intervals cross zero at every horizon, and both remain worse than the train median in absolute MAE. V004 remains useful as structural evidence/control, not as a promoted scalar forecaster.

## E-MARKET-DIST-V006 — empirical distributional foundation

**Hypothesis frozen before benchmark:** rescaling a train-only empirical standardized-return shape by causal 20-session asset volatility improves origin-day-equal pinball loss over the unconditional train empirical distribution.

Versions:

```text
benchmark       market_brain_distributional_v006_baseline_v001
model           market_brain_distributional_v006_empirical_baselines_v001
market features market_daily_state_v003_core
labels          market_daily_reaction_v003_core
dataset         market_daily_v003_all_asset_days_current_cohort_research
seed            42
bootstrap unit  origin_trading_day
```

Five purged expanding folds are used. For every fold:

```text
latest training target day < first test origin day
```

Primary result:

| Horizon | Daily-equal pinball delta | CI block 5 | CI block 10 | CI block 20 |
|---:|---:|---:|---:|---:|
| H1 | +0.009198 | [+0.007104, +0.011483] | [+0.006543, +0.011498] | [+0.006220, +0.011669] |
| H3 | +0.013490 | [+0.009037, +0.018161] | [+0.008329, +0.018274] | [+0.007649, +0.018225] |
| H5 | +0.013420 | [+0.007878, +0.019504] | [+0.007085, +0.019961] | [+0.006526, +0.019882] |
| H10 | +0.012663 | [+0.004013, +0.023010] | [+0.002568, +0.024809] | [+0.000896, +0.022956] |

Temporal fold sign counts:

```text
H1  5/5 positive
H3  5/5 positive
H5  5/5 positive
H10 4/5 positive; earliest fold -0.003397 pp
```

**Result:** primary passes at all four horizons. The candidate central interval coverage is close to nominal, but the median and positive-return probability do not improve. No learned model, event feature, graph feature, external proxy, macro feature, cost model or path model was tested.

**Interpretation:** retain V006 as the first supported conditional-dispersion baseline. Do not interpret the result as directional alpha, expected-return skill, a trajectory forecast or production readiness.

Artifacts:

```text
config/market_brain_distributional_v006.json
reports/market_brain_distributional_v006/empirical_baseline_v001/benchmark_plan.json
reports/market_brain_distributional_v006/empirical_baseline_v001/benchmark_summary.json
reports/market_brain_distributional_v006/empirical_baseline_v001/h{1,3,5,10}_benchmark.json
reports/market_brain_distributional_v006/empirical_baseline_v001/h{1,3,5,10}_primary_daily_losses.csv
```


<!-- MARKET_DIST_V0061_ROBUSTNESS_V001_START -->
## E-MARKET-DIST-V0061 — robustness/falsification preregistration

**Status:** preregistered; results intentionally not yet interpreted.

V006 remains the completed primary. V006.1 does not change its model, target, quantiles, folds, primary unit or claim. It must first reproduce the frozen V006 OOS daily losses and fail if reproduction differs beyond the configured numerical tolerance.

Predeclared diagnostics:
- fold and quantile-specific pinball/calibration;
- direct V006 vs `asset_empirical` comparison;
- asset and sector contribution concentration plus leave-one-group-out sensitivity;
- low/mid/high volatility regimes defined only from each outer fold's training `asset_vol_20d_pct`;
- non-overlapping 126-origin-day calibration blocks within each outer fold;
- alternative causal scale sensitivities using `asset_vol_5d_pct` and `asset_vol_63d_pct`;
- moving-block bootstrap on origin-day losses at 5/10/20 days where a comparison is inferentially summarized.

Alternative scales are diagnostics only and cannot retroactively replace the V006 `vol20` primary. No event, graph, macro, external proxy, cost, path or new learned-model feature enters V006.1.
<!-- MARKET_DIST_V0061_ROBUSTNESS_V001_END -->

<!-- MARKET_DIST_V007_ADAPTIVE_TAIL_V001_START -->
## E-MARKET-DIST-V0061 — completed robustness interpretation

**Status:** complete; source V006 reproduced exactly on all horizons.

V006.1 supports the broad conditional-dispersion claim but narrows its functional form:
- leave-one-asset and leave-one-sector deltas remain positive at every horizon, so the V006 vs global gain is not driven by one asset or one sector;
- `asset_vol_5d_pct` is decisively worse than V006 at all horizons;
- `asset_vol_63d_pct` is a stronger sensitivity than V006 at H3/H5/H10 and directionally stronger at H1;
- the benefit is asymmetric across tails: the upper tail improves strongly while q05/q25 deteriorate at longer horizons;
- low-volatility regimes are under-covered and high-volatility regimes are over-covered, consistent with an overly linear scale response;
- `asset_empirical` remains a serious structural reference, especially at longer horizons.

These findings do not retroactively replace V006. They define the preregistered hypothesis for V007.

## E-MARKET-DIST-V007 — adaptive asymmetric asset-scale preregistration

**Status:** preregistered; no V007 performance interpreted yet.

V007 keeps q50 at the global training median and learns only distribution shape/scale. For each outer fold it uses a nested temporal validation split to select separate downside and upside parameters. The asset supplies a structural tail anchor; current 20d/63d volatility supplies a normalized dynamic state.

For side `s` in {downside, upside}:

```text
u_i,t = lambda20 * log(vol20_i,t / median_train_i(vol20))
      + (1-lambda20) * log(vol63_i,t / median_train_i(vol63))

g_s(i,t) = kappa_s * exp(alpha_s * u_i,t)
```

For q<0.5 or q>0.5:

```text
Q_q(i,t) = global_train_median
         + (asset_train_Q_q - asset_train_Q_50) * g_s(i,t)
```

q50 is never dynamically learned in V007.

Nested selection minimizes origin-day-equal pinball on q05/q25 and q75/q95 separately. Primary outer reference is the predeclared `vol63_scaled_empirical`; V006 `vol20`, `asset_empirical` and global empirical remain secondary controls. All four horizons must be reported.
<!-- MARKET_DIST_V007_ADAPTIVE_TAIL_V001_END -->

<!-- MARKET_DIST_V007_ZERO_VOL_AMENDMENT_V0011_START -->
## V007 pre-performance implementation amendment — exact zero volatility

The first V007 H1 benchmark attempt aborted during data loading before any OOS metric was produced. Core V003 permits exact zero rolling volatility, while the initial V007 loader incorrectly required both vol20 and vol63 to be strictly positive.

V007.0.1 corrects only this domain handling. Exact observed zero volatility is mapped to the already-frozen lower log-ratio clip; a nonpositive per-asset TRAIN median normalizer falls back to the positive global TRAIN median. The vol20 control restores the completed V006 `global_empirical_fallback` behavior for nonpositive scale rows, and vol63 uses the same prospective control rule. Negative/null volatility remains a hard error.

No rows are dropped, no epsilon is introduced, and no alpha/lambda/kappa grid, feature, primary reference, quantile, horizon, fold or score is changed. The plan gate now reports zero/negative/null scale support from the real Core V003 DB. This amendment must be committed before rerunning V007.
<!-- MARKET_DIST_V007_ZERO_VOL_AMENDMENT_V0011_END -->

<!-- MARKET_DISTRIBUTIONAL_V008_V001_START -->
## Market Distributional V008 — Conditional Residual Quantiles

- Version: `market_brain_distributional_v008_conditional_residual_quantiles_v001`
- Target: H1/H3/H5/H10 terminal `return_pct` distribution.
- Residualization: `(return - development_train_median) / asset_vol_63d_pct` on positive-scale rows.
- Learner: `HistGradientBoostingRegressor(loss=quantile)` for q05/q25/q50/q75/q95.
- Capacity: two frozen regularized profiles; selection only in nested temporal validation.
- Training weights: equal total weight per origin trading day.
- Calibration: final 126 origin days of each outer-train period, purged from model development; quantile-specific weighted residual shifts in standardized space.
- Primary reference: equivalently recent-calibrated vol63 empirical distribution.
- Primary candidate: full endogenous Core V003 state.
- Diagnostics: same selected capacity on scale-only and own-state features; no post-hoc rescue.
- Proper score: equal-origin-day mean pinball; 5/10/20-day moving-block bootstrap; calibration/coverage always reported.
- Claim boundary: developmental current-cohort historical reconstruction, not strict PIT, not direction/profitability/path/production evidence.
<!-- MARKET_DISTRIBUTIONAL_V008_V001_END -->

<!-- MARKET_V008_SPLIT_FEASIBILITY_V0011 -->
### Market Distributional V008 v0011 — pre-performance split-feasibility amendment

The original V008 v001 benchmark aborted before any model fit or OOS performance metric because the earliest 30% outer fold could not simultaneously satisfy 126 recent calibration origin days, 126 minimum nested validation origin days, and 500 minimum nested training origin days after purging. No V008 performance was observed.

V008 v0011 preserves the frozen scientific question, features, H1/H3/H5/H10, five 30%-initial purged expanding outer folds, 126-day recent calibration window, 126-day minimum inner validation, HGB profile set, vol63_recent_calibrated primary reference, metrics, bootstrap and gates. The only scientific-control change is `minimum_inner_train_origin_days: 500 -> 378` (1.5 trading years) for nested profile selection. Final fold models remain fit on the full development block. The plan now performs a clock-only conservative split-feasibility audit before benchmarking.

## E-MARKET-DIST-V008 — completed conditional residual quantiles

**Status:** complete; no promoted model.

Frozen primary:

```text
hgb_full_endogenous_calibrated
vs
vol63_recent_calibrated
```

Result:

| Horizon | Daily-equal pinball delta | Block-10 95% CI | Gate |
|---:|---:|---:|---|
| H1 | -0.005920 | [-0.009783, -0.001948] | FAIL_SIGNIFICANT |
| H3 | -0.017368 | [-0.025117, -0.010092] | FAIL_SIGNIFICANT |
| H5 | -0.025534 | [-0.039537, -0.012224] | FAIL_SIGNIFICANT |
| H10 | -0.068362 | [-0.096638, -0.038895] | FAIL_SIGNIFICANT |

The full candidate also loses to raw vol63 at all horizons. The recent
calibration of raw vol63 is harmful at every horizon. Same-capacity full
learners lose to both scale-only and own-state controls. All 20 nested
selections choose the shallow regularized profile.

Interpretation: reject the V008 contract. Do not generalize the result to every
possible endogenous representation.

## E-MARKET-DIST-V0081 — endogenous closure preregistration

**Status:** infrastructure complete; performance intentionally not run.

Versions:

```text
benchmark       market_brain_distributional_v0081_endogenous_closure_v001
model           market_brain_distributional_v0081_hgb_own_state_raw_v001
source          market_brain_distributional_v008_conditional_residual_quantiles_v0011
market features market_daily_state_v003_core
labels          market_daily_reaction_v003_core
dataset         market_daily_v003_all_asset_days_current_cohort_research
bootstrap unit  origin_trading_day
```

Primary question:

```text
H1 hgb_own_state_raw vs vol63_raw
```

Both are fit from the complete purged outer train. The candidate predicts
q05/q25/q50/q75/q95 of
`(return_pct-train_median)/asset_vol_63d_pct` using the exact frozen
14-feature own-state family. No post-model calibration or hyperparameter
selection is allowed.

The H1 capacity control uses five seeds. It retains aligned
vol5/vol20/vol63 and jointly deranges all other own-state feature vectors across
assets within each origin day before fitting the identical HGB profile.

A developmental pass requires:

- positive block-10 lower confidence bound vs raw vol63;
- candidate calibration error no worse;
- at least 4/5 positive folds;
- at least three quantiles with positive daily-equal point deltas;
- positive block-10 lower confidence bound vs mean placebo loss;
- positive point delta vs every placebo seed.

H3/H5/H10 are mandatory diagnostic reports and cannot rescue H1. The current
cohort is not survivorship-free, Core V003 is not strict PIT, and the historical
sample was already inspected in V008. Fresh temporal confirmation is required
before any promotion.
## E-MARKET-DIST-V0081 -- completed endogenous closure

**Status:** complete; developmental H1 pass, no promoted production model.

Primary H1 result:

```text
raw vol63 minus own-state HGB pinball     +0.004703 pp
block-10 95% interval                    [+0.002923,+0.006582]
positive folds                           4/5
improved quantiles                       4/5
positive assets/sectors/years            466/497, 11/11, 6/7
all six frozen gate checks               PASS
```

The candidate beat the mean five-seed capacity placebo with a positive
block-10 interval and beat every placebo seed by point estimate. q50, median
MAE and positive-return Brier worsened; the supported incremental object is
distribution shape/tails. H3 corroborated; H5/H10 were inconclusive under
dependence-aware intervals. Historical reuse makes the pass developmental.

## E-MARKET-DIST-V009 -- prospective temporal confirmation

**Status:** preregistration PASS; registry initialized; pre-holdout fit frozen; 2026-08-28 source bar rejected and not sealed; refresh V002 real 5-asset check PASS; first sealed batch pending no earlier than 2026-08-31.

Versions:

```text
benchmark        market_brain_distributional_v009_prospective_holdout_v001
model            market_brain_distributional_v009_hgb_own_state_static_v001
reference        market_brain_distributional_v009_vol63_raw_static_v001
registry         prospective_prediction_registry_v001
evaluation       market_brain_distributional_v009_evaluation_v001
features         market_daily_state_v003_core
labels           market_daily_reaction_v003_core
```

Contract:

- fixed 497-asset universe snapshot on 2026-08-24;
- one pre-holdout fit, with targets ending before 2026-08-28;
- no refit/calibration/feature selection during confirmation;
- prediction seal no later than 16 hours after state close;
- no retroactive backfill or skipped eligible origins;
- immutable predictions and separate outcomes/scores;
- first 126 origins descriptive; first 252 origins formal;
- raw vol63 reference; equal-origin-day pinball; 5/10/20-day moving blocks;
- at least 4/5 positive time blocks, three improved quantiles and calibration
  not worse for confirmation.

V009 can confirm only market-only H1 terminal-return distribution improvement.
It cannot confirm direction, alpha, profitability, paths, survivorship-free
generalization, event value or graph value.

<!-- EXPECTATION_CAPTURE_FOUNDATION_V001_START -->
## DATA-EXPECTATION-CAPTURE-V001 — prospective information vintage foundation

**Type:** data foundation, not predictive experiment.

**Purpose:** persist immutable, causally timestamped source observations, scheduled-event revisions, expectation/guidance snapshots and reported economic facts in a database isolated from V009/Market Core.

**Claim boundary:** infrastructure/capture lineage only. No alpha, information-value or model-performance claim is permitted.
<!-- EXPECTATION_CAPTURE_FOUNDATION_V001_END -->

## DATA-EVENT-DISTRIBUTIONAL-V001 — close-aligned preparation, not a model

**Status:** full data run completed; temporal contract REJECTED, artifacts
preserved, not trainable. No training or predictive result. Of 2,001 states,
1,886 were admitted and 115 quarantined; 169 admitted states were incorrectly
shifted more than one day by HTTP modification metadata. The 4,104 rows across
three scenarios are not independent samples and the old integrity PASS does not
certify this rejected clock rule. See D031 and V002 for the correction.

- dataset: `distributional_event_close_aligned_v001`;
- source events: `event_state_v0031_deep`;
- market state: `market_daily_state_v003_core`;
- source labels: `market_daily_reaction_v003_core`;
- output labels: `event_distributional_close_aligned_v001`;
- projection: `event_arrival_set_v001`;
- frozen data end: 2026-08-24, excluding V009 prospective data;
- origin: first close strictly after information availability;
- delays 0/3600/86400 seconds: separate sensitivity cohorts, not independent rows;
- strict PIT: false; first public disclosure not established;
- model/folds/seeds/bootstrap: none selected or executed in this data stage.

The output separates features, labels, evidence lineage and exclusions. An
integrity PASS is not an information-value gate. Before model fitting, freeze
chronological purged folds, event/filing/content separation, capacity controls,
proper scores and dependence-aware uncertainty. Never apply the final V009
fit retrospectively to this historical sample.

Contract and execution: [DISTRIBUTIONAL_EVENT_DATASET_V001.md](DISTRIBUTIONAL_EVENT_DATASET_V001.md).

## DATA-EVENT-DISTRIBUTIONAL-V002 — provenance-aware clock correction

**Status:** complete materialization and exclusion review; integrity PASS,
scientific REVIEW, data preparation only. Ready for a separate preregistration,
not authorized for ad-hoc training and not a model result.

- dataset: `distributional_event_close_aligned_v002`;
- source snapshots: `event_state_v0031_deep`, unchanged;
- source market/labels: `market_daily_state_v003_core` /
  `market_daily_reaction_v003_core`, unchanged;
- derived labels: `event_distributional_close_aligned_v002`;
- derived projection: `event_arrival_set_v002`;
- clock policy: `sec_acceptance_proxy_http_metadata_separate_v002`;
- research end: 2026-08-24; strict PIT=false; no first-public disclosure claim;
- model, folds, seeds, bootstrap: not selected or run.

Full run:

- source/examined states: 2,001 / 2,001;
- eligible states: 1,885; unexplained clock shifts: zero;
- quarantined: 115 cross-accession plus one AAPL same-file multi-reference;
- zero-delay selected state links / samples: 1,734 / 1,365;
- all-scenario rows: 4,086, not independent;
- usable zero-delay H1/H3/H5/H10: 1,351 / 1,315 / 1,249 / 1,032;
- exact-Core exclusions: 151 early states; no later-date substitution;
- H10 zero-delay corporate-action exclusions: 332 (24.3%);
- dataset SHA-256:
  `c3a2d89fa127863d4a2477ffe9a41fe0b0e5cd0f203b07fd05028760b43c7098`.

D033 freezes the exclusions and closes the data-review gate. Next: separate
plan-only preregistration with temporal/group purges, proper scores,
capacity-matched controls and dependent uncertainty. The old V001 output and
source DBs remain preserved. No V009 fit/prediction changes.
Details: [DISTRIBUTIONAL_EVENT_DATASET_V002.md](DISTRIBUTIONAL_EVENT_DATASET_V002.md).

## DATA-MARKET-TEMPORAL-V001 — horizon-conditioned outcome foundation

**Type:** deterministic data preparation and selection audit; no model.

**Status:** full configured-sparse materialization complete; integrity and exact
Core parity PASS; raw-close long-horizon selection rejected as primary target.

Frozen sources:

```text
data/database/market_data_v2.db                    read-only
data/processed/market_daily_v003_core.db           read-only
market_daily_state_v003_core                       state version
market_daily_reaction_v003_core                    parity label version
raw_close_t_to_raw_close_t_plus_h                   target semantics
```

Output contract:

```text
market_temporal_horizon_conditioned_outcomes_v001
market_temporal_terminal_return_v001
tau_sessions integer 1..252
```

The full artifact contains 1,092,555 states, 497 assets and 18,573,435 outcomes
at 17 taus. Dense H1..H252 would contain 275,323,860 outcomes. All 4,370,220
H1/H3/H5/H10 reference rows match exactly with no missing rows. Resolved action
overlap is 26.25%/76.20%/79.09%/80.32% at H21/H63/H126/H252; the H252 median
asset overlap is 100% and sector selection is severe.

Training remains blocked. V001 is retained as exact raw-close/no-action evidence
and control for V002; it is not patched or deleted. V009 is not opened, reused
or modified. See [TEMPORAL_DATASET_V001.md](TEMPORAL_DATASET_V001.md).

## DATA-MARKET-TEMPORAL-V002 — explicit total shareholder return foundation

**Type:** deterministic outcome reconstruction and data audit; no model.

**Status:** full sparse artifact and mechanical review complete; economic
special-action decisions pending; no model.

Read-only inputs:

```text
data/database/market_data_v2.db
data/processed/market_daily_v003_core.db
data/processed/market_temporal_v001.db
```

Output contract:

```text
market_temporal_horizon_conditioned_total_return_v002
market_temporal_total_shareholder_return_v002
tau_sessions integer 1..252
```

Economic one-session factor:

```text
(provider_close_t + cash_distribution_t) / provider_close_t_minus_1
```

Provider Close/distributions are already split-normalized; split factors remain
lineage and are not multiplied. Adjusted Close is audit-only. Its separate
`Close_t/(Close_(t-1)-cash_t)` control must match the observed adjusted factor
within `2e-6` to validate provider action timing/units.

Hard gates are full V001 parity across every materialized tau, exact no-action
H1/H3/H5/H10 identity, action-step reconciliation, read-only stable inputs,
atomic/idempotent publication and V009 isolation. Four synthetic tests pass,
including special-distribution mathematics, recovered dividend/split windows,
provider-control failure blocking and V001 tamper blocking.

Real result: 18,573,435 outcomes; zero V001/no-action mismatches; 15,299
provider-reconciled actions plus four grid-start actions; zero quarantined
resolved outcomes. The downstream review passes distribution/support/arbitrary
tau gates and flags 16 cash steps >=5% (five >=10%). Eleven can enter
model-visible outcomes and require decisions; five are pre-origin lineage only.
The V002 internal block remains immutable.
See [TEMPORAL_DATASET_V002.md](TEMPORAL_DATASET_V002.md).

## E-MARKET-TEMPORAL-DIST-V001 — frozen horizon-conditioned runner

**Status:** CLOSED NEGATIVE. The development folds were executed and aggregated, resulting in a firm failure against the parsimonious `vol63 + tau` baseline. The single representation was unable to capture central mass (q50) and degraded significantly at long horizons (H126, H252). The holdouts remain strictly sealed and the branch has been closed to prevent any data dredging or parameter tweaking. No model promotion.

## E-MARKET-TEMPORAL-DIST-V002 — residual shrinkage model preregistration

**Status:** CLOSED NEGATIVE after five valid development folds. The aggregate
gate is `FAIL_CLOSE_TEMPORAL_DISTRIBUTIONAL_V002_BRANCH`; H7/H17/H42/H90/H180
were never read and remain strictly sealed.

The candidate learns q-specific residuals in log-total-wealth space over a
`vol63 + tau` base. Base residual targets use five past-only, purged expanding
internal folds; ordinary/random K-fold is forbidden. The final correction is
multiplied by the non-tunable `2 ** (-(tau-1)/63)` before log-space monotone
rearrangement and percent back-transformation.

The sole scientific contract is
`config/temporal_distributional_preregistration_v002.json`; the minimal runner
contract is bound to it by SHA-256. Development cannot pass unless the primary
252-day block interval, breadth, calibration and placebo gates pass and neither
H126 nor H252 has a negative point delta versus the base. Four of five sealed
taus and H180 no-harm are required if the one-time holdout is later opened.

Real preflight: 1,092,555 origins, 3,277,665 selected training-anchor rows
before rowwise purge, maximum/minimum anchor ratio `1.00413`, stable read-only
V002/Core inputs, no holdout performance read and no V009 access. This tests a
new outcome-informed hypothesis after V001; it does not rescue V001 or imply
smooth unseen-tau interpolation, coherent paths, alpha or production value.

The developmental target is `Q_q(total_return | own market state,tau)`. Twelve
anchors are development data and H7/H17/H42/H90/H180 are sealed interpolation
holdouts. The candidate uses the frozen V008.1 shallow profile with explicit
tau coordinates; the primary control sees tau plus vol63 and five same-capacity
placebos preserve volatility while deranging other own state within origin day.

Primary folds use 2,008 H252-resolved days through 2025-08-21. Five expanding
test blocks contain 281-282 origin days and 134,810-138,777 common-support
states. Training is purged rowwise by target end. The recent 252-day censored
tail is secondary per-tau support only. Primary uncertainty resamples whole
origin-day panels in 252-session blocks. Passing remains developmental and
cannot establish path, direction, alpha, profitability, PIT, production or a
V009 claim.

The fit target is `log1p(total_return_pct/100)` with exact inverse scoring and
no clipping. SHA-256 of state id selects three balanced anchors per origin for
fit; OOS development retains all 12. A candidate must also beat five
same-capacity within-day derangement placebos. An exact development PASS freezes
all code/config/evidence/mask/model/prediction/report hashes before the five
unseen taus can open once without refit or contingency.

Observed terminal result: reference-minus-candidate pinball delta
`-0.008501 pp`; 252-session moving-block 95% interval
`[-0.023782, -0.003623]`; 2/5 positive folds, 3/12 positive anchors and 2/5
positive quantiles. H126/H252 are `-0.033994/-0.012952 pp`. Candidate
calibration improves from `0.006868` to `0.003893` and the candidate beats all
five derangement placebos, but these secondary successes cannot rescue the
failed primary. `development_closure.json` is `CLOSED_NEGATIVE`; no model is
promoted.

## DATA-INFORMATION-INTEGRATION-V001 — causal information inventory

**Type:** read-only schema, coverage, availability and integration audit; no
dataset and no model.

**Status:** real local read-only execution passed on 2026-08-31; no model or
feature promoted.

The configured sources are Market Core V003, market/source V2, SPY/QQQ/IWM
external state, VIX/rates/credit conditions, corrected Event Dataset V002,
strict-PIT information capture and entity/relation evidence. The V009 registry
is forbidden and not opened.

Outputs separate inventory, Core coverage, causal/model eligibility, acquisition
gaps and a plan-only shared-state design. Hard gates require read-only/stable
inputs, unique Core identities, exact day-context coverage, valid strict-PIT
capture clocks and no future/outcome feature names. A PASS authorizes only
review of a later context materializer; training remains false.

Observed inventory: 1,092,555 Core states for 497 assets over 2,260 sessions;
1,250,027 daily OHLCV asset-days for 508 assets; 1,111,944 one-minute legacy
bars spanning only seven trading days; 62,671 legacy news documents with no raw
text or document-level causal availability clock; a corrected SEC event track
for ten assets; prospective expectations for 19 Core assets; and graph evidence
for ten registrants with zero canonical identity buckets and zero edge-ready
claims. External-market and financial-condition features have exact, non-null
Core-domain coverage.

See [INFORMATION_INTEGRATION_V001.md](INFORMATION_INTEGRATION_V001.md).

## DATA-PUBLIC-INFORMATION-INTAKE-V001 — public bars/news source acquisition

**Type:** isolated data acquisition/catalog/audit foundation; no model.

**Status:** implementation, synthetic tests and real DuckDB integration tests
PASS. The remote bars manifest is frozen and its download dry-run passed;
bulk downloads and the gated news manifest are user-run. No source or feature is promoted.

Configured initial scope:

```text
CryptoSpartan/stocks_bars_1m
  fixed revision b21d46e47ea2f39801d174ca850af76999cc5113
  full one-minute Parquet

Brianferrell787/financial-news-multisource
  main resolved/pinned at manifest time
  priority finance subsets before optional full corpus
```

The catalog records source revision, selected files, sizes/hashes, rights and
causal status, download runs and structural audits. Downloads are resumable,
content-shared across profiles and bounded by a non-preallocated 100 GiB cap.
Raw payloads never enter `market_data_v2.db`; V009/Core are protected.
Alpaca feed/opening/adjustment semantics and news rights/time precision/dedup
remain explicit next gates. Median price synthesis, source overwrite, volume
blending, feature materialization and training are forbidden.

See [PUBLIC_INFORMATION_INTAKE_V001.md](PUBLIC_INFORMATION_INTAKE_V001.md).

## DATA-PUBLIC-INFORMATION-SEMANTICS-AUDIT-V001 — source meaning census

**Type:** read-only bar/news semantics, time, identity, deduplication and Core
coverage audit; no materializer and no model.

**Status:** real full-corpus execution `PASS_READ_ONLY_SEMANTICS_REVIEW_READY`;
inputs unchanged, training/materialization false and V009 interaction `NONE`.

The audit covered 531,912,667 one-minute rows and 28,741,192 news-link rows.
It separated FNSPID collection lineage from 20 document domains and 1,142
publisher/byline values inside that collection; measured 15,859,669 excess URL
rows; classified historical clocks; and retained graph evidence as noncanonical.
Bars cover 476/497 Core current symbols. Cross-source return agreement is tight
relative to huge split-like raw-level differences, so source-specific values
are retained and adjustment reconciliation is required.

PASS authorizes only design of Public Information Canonical Lake V002. The
next scientific model remains blocked until document/story identity, historical
ticker validity, session/adjustment semantics and causal availability are
materialized and audited.

See
[PUBLIC_INFORMATION_SEMANTICS_AUDIT_V001.md](PUBLIC_INFORMATION_SEMANTICS_AUDIT_V001.md).
