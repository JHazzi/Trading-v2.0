# Information Source Strategy V001

## Purpose

The next research bottleneck is information, not learner capacity. V006 established reproducible information about conditional scale. V007 and V008 showed that increasingly elaborate endogenous price/volume representations did not earn robust multi-horizon improvement over strong volatility baselines. The parallel acquisition branch therefore begins collecting observables that correspond to investor beliefs and uncertainty while V009 remains frozen.

This document is **not** a predictive preregistration. Captured observations are model-invisible until a later experiment explicitly freezes target, feature transform, baseline and gates.

## Latent concepts and observable proxies

- **Expectation / belief:** analyst EPS and revenue estimates, management guidance.
- **Revision:** changes in the same expectation series across retrieval times.
- **Disagreement:** high-low estimate range and analyst count when available.
- **Scheduled uncertainty:** known earnings/event date or time window.
- **Surprise:** future `actual - last legitimate pre-event expectation`; never hard-code its market sign.
- **Market-implied uncertainty/tails:** option IV surface, skew, term structure and risk reversals.
- **Macro information set:** vintage-correct economic observations and release chronology.
- **Factual truth/evidence:** original SEC filing evidence and reported facts.

## First live source: earnings expectations

Alpha Vantage officially documents `EARNINGS_ESTIMATES` as annual/quarterly EPS and revenue estimates with analyst count and revision history, and `EARNINGS_CALENDAR` as future earnings dates. V001 treats every API response as a provider observation captured at retrieval time. Vendor historical estimate data is **not** automatically historical PIT.

The calendar endpoint may provide only a date/daypart. V001 deliberately does **not** manufacture an exact `scheduled_for` timestamp from that. It stores the raw source snapshot; a future additive contract can represent date/daypart/window precision explicitly.

The initial ten-symbol estimate pilot is the existing deep Event research cohort so later work can join expectations to richer SEC evidence without expanding several research dimensions at once.

## Provider sequencing

1. Alpha Vantage earnings expectations: begin prospective belief/revision accumulation.
2. SEC EDGAR: official evidence/actual/guidance backbone and cross-checking.
3. Options pilot (Massive or licensed equivalent): market-implied uncertainty and tail pricing.
4. ALFRED/FRED: historically vintage-aware macro state.
5. Cboe DataShop if budget justifies historical option surfaces.
6. FMP as an alternate estimates vendor only after vintage/revision semantics and licensing are confirmed.

## Isolation rule

The acquisition code may write only to `information_capture_v001.db` and reports specific to acquisition. V009 code/config must never import or reference this branch. No feature transform is permitted until a separate experiment is preregistered.
