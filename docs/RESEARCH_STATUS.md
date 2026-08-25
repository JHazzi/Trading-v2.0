# Research Status — 2026-08-25

**Status:** canonical empirical checkpoint  
**Scope:** research, not production trading

## 1. Executive summary

The project has completed the first serious deep SEC Event Brain research corpus.

The data/lineage infrastructure is now substantially stronger than the predictive models.

Current high-level conclusion:

> The SEC Event State contains a **weak candidate incremental signal around 10 sessions**, but this is not statistically confirmed and the current daily Market Brain remains too weak. The next work is robustness/falsification and a stronger market base distribution, not more SEC data or a larger model.

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
