# Event Brain v0.1 — first predictive slice

## What this is

This is the first model-facing implementation after the causal infrastructure.

The research question is:

> Given the market state that was already observable and the event evidence
> actually available at time t, does event context add predictive information
> about the subsequent realized return?

The first benchmark is intentionally simple:

Market-only:
    M(X_t, T)

Event-only:
    E(E_t, T)

Incremental fusion:
    M(X_t, T) + F(X_t, E_t, T)

`F` is trained on residuals generated from chronological out-of-fold Market
predictions. It is not trained on a random split.

This is not yet the final distribution/trajectory engine. Its purpose is to
prove or falsify incremental event signal before adding complexity.

## Event state

State snapshots are created at:
- the first normalized event observation availability;
- every subsequent evidence arrival linked to that event.

Therefore 100 repeated articles do not become 100 events. They change one
event's evidence state over time.

State stores descriptive facts:
- event type/scope;
- evidence count;
- distinct source count;
- official statement / reported fact / opinion / forecast / rumor /
  speculation / correction / retraction counts;
- time since first evidence;
- known occurrence/scheduled timing when available.

It deliberately does not store:
- source reliability;
- economic importance;
- bullish/bearish weight;
- fixed event impact;
- fixed decay.

Those are learned later from outcomes.

## Temporal event mapping

`occurred_at` and `available_at` remain different variables.

An event may happen at 14:00 and first become defensibly known at 14:07.
Predictions before 14:07 cannot use it.

For SEC v0.1 the filing acceptance is information availability. It is NOT
automatically treated as the time the underlying economic event occurred.

## Reaction labels

Daily reaction labels use:
- last completed session close before event state;
- 1 / 3 / 5 / 10 future sessions;
- raw unadjusted OHLCV;
- quality-gated price observations;
- explicit exclusion of corporate-action overlap.

Intraday event states are excluded from daily labels by default because daily
bars cannot isolate the part of the session after the event. Intraday targets
will use the existing minute/session pipeline where coverage exists.

## Multi-source future

SEC is only the first deterministic normalization adapter.

The same normalized contract must later receive:
- company Investor Relations / press releases;
- reputable news wires/newspapers where access permits;
- exchange notices/halts;
- macro release/vintage sources.

A news source is not assigned a fixed prestige score. Source/context usefulness
is learned from historical incremental outcomes.

## Graph propagation

Direct event-to-asset/entity links are kept separate from indirect propagation.
A TSMC event does not automatically become an NVIDIA event.

Later:
    direct event -> structural/statistical/learned graph -> related asset context

The graph layer will add supplier/customer/competitor/regulatory and learned
lead-lag features without converting co-occurrence into causality.

## Continuous learning

As soon as Event Brain v0.1 produces real predictions, persist:
prediction -> outcome -> error/calibration -> rolling diagnostics -> drift

Automatic retraining is NOT blind online mutation. Later:
candidate training -> walk-forward -> champion comparison -> promotion/reject
-> rollback if needed.
