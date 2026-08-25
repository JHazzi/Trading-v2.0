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
