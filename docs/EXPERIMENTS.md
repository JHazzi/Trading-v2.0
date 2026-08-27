# Experiment Registry

This file summarizes major scientific experiments. Detailed machine-readable outputs remain under `reports/` and `data/processed/`.

Do not overwrite historical report directories when introducing a new experiment version.

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
