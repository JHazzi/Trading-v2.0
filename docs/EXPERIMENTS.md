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

## Next experiment — E-EVENT-V0021-ROBUSTNESS

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
