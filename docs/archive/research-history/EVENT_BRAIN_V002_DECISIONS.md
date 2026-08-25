# Event Brain v0.2 — design decisions against ARCHITECTURE.md

## Purpose

This is the first multi-company predictive benchmark after the causal SEC
pipeline produced 308 normalized events, 320 event states and more than 200
usable labels at every 1/3/5/10-session horizon.

The goal is NOT to prove profitability. It is to answer a narrower scientific
question:

> Do causal event features add out-of-sample predictive information beyond a
> market-only baseline of comparable capacity?

This follows the Architecture criterion that each new source must demonstrate
incremental out-of-sample information before the project moves toward trading.

## 1. Market model still works without events

Market context v002 contains only information known by the event state time:

- target asset 1/3/5/10/20-session returns;
- target 5/20-session realized volatility;
- range, volume ratio and distance to 20-session high/low;
- leave-one-out cross-sectional returns, breadth and dispersion;
- same-sector peer context, excluding the target asset;
- asset-vs-market and asset-vs-sector divergence.

The target asset is explicitly removed from cross-sectional and sector peer
aggregates. This prevents circular "market context" features from partially
copying the asset whose return is being predicted.

The current sector sample is intentionally small. With two pilot companies in
most sectors, sector context often means one peer. `sector_peer_count` is
therefore an input, not hidden.

## 2. Why no SPY / sector ETFs yet

Adding benchmark ETFs now would improve Market Brain, but it would change the
data universe at the same time as Event Brain changes. v0.2 first measures the
incremental value of events using the data already collected.

If the event candidate appears useful, the next robustness test must strengthen
Market Brain with broader index/sector/macro context before attributing durable
alpha to events.

## 3. Earnings can be positive while price falls

v0.2 does NOT hardcode:

    earnings beat -> bullish

It can already observe part of the "priced-in" context:

- pre-event run-up / drawdown;
- target vs sector;
- target vs cross section;
- market breadth / dispersion / volatility.

This allows the event adjustment to learn context-dependent reactions.

However, v0.2 still does NOT possess analyst consensus, prior guidance,
estimate revisions or implied expectations. Therefore it cannot yet explicitly
model:

    surprise = actual - expectation

That remains a real missing feature family, not something inferred or fabricated.

## 4. Reliability and sensationalism

No source receives a fixed reliability score.

SEC-only historical evidence is also insufficient to learn cross-source
reliability. Future multi-source work should distinguish:

- factual reliability;
- novelty;
- corroboration;
- predictive utility beyond Market Brain;
- market impact;
- persistence.

Sensationalism/framing should be represented through observable text/evidence
features (headline-body disagreement, certainty language, hedging,
corroboration, correction history, etc.) and then evaluated historically. It
must not be a manually assigned "yellow journalism score".

## 5. Evaluation leakage controls

### Event grouping

All snapshots of one `event_id` stay on the same side of an outer fold.

### Purging overlapping labels

For a test block beginning on day D, training rows are allowed only when:

    target_trading_day < D

This matters for 3/5/10-session labels. A chronological split based only on
state_time can leak outcomes whose realization extends into the test period.

### Walk-forward

Four expanding, event-grouped, purged outer folds are the default. No random
train/test split is used.

### Day-block bootstrap

Confidence intervals resample `origin_trading_day`, not individual rows. Events
on the same market day are not treated as independent observations.

## 6. Capacity-control benchmark

A critical confound is model capacity.

If we compare:

    Market
    Market + second residual model(Event, Market)

the second system may win simply because it has another nonlinear learner.

Therefore v0.2 evaluates:

1. Zero baseline
2. Market-only
3. Event-only
4. Market + residual(Event)
5. Capacity control = Market + residual(Market)
6. Contextual event = Market + residual(Market, Event)

The PRIMARY comparison is:

    Capacity control
        vs
    Contextual event

Both have a second residual learner. The difference is whether event features
are available.

A positive event result means:

    MAE(capacity_control) - MAE(contextual_event) > 0

and should ideally have a day-block bootstrap CI above zero.

## 7. What a positive result does NOT mean

It does not mean production-ready.

The current event history begins in 2024 because the pilot intentionally loaded
roughly 30 recent filings per company. This misses older regimes including
COVID and much of 2022.

All current event evidence is historical research reconstruction, not strict
live PIT capture.

The current model predicts a point return, not the final Architecture target of
a conditional trajectory distribution with q05/q25/q50/q75/q95 and calibrated
probabilities.

## 8. Decision after benchmark

### If event features help consistently

Next:
1. expand SEC history backward;
2. strengthen Market Brain using broader market/sector context;
3. rerun the same purged benchmark;
4. only then add a second source family (company IR / official releases);
5. measure incremental contribution again.

### If event features do not help

Do not add a larger neural network.

First diagnose:
- event taxonomy quality;
- event-state information content;
- weak Market Brain baseline;
- lack of expectation/surprise data;
- limited historical regimes;
- event-type-specific effects.

Then change one layer at a time and rerun the same evaluation contract.

## 9. Production remains separate

This package creates a candidate evaluation artifact and OOS predictions. It
does not promote a model, emit trading actions or mutate the production
champion.

Prediction persistence, outcome tracking, calibration, drift and
candidate/champion promotion remain later stages of the Architecture.
