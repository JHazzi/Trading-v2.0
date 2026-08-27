# Expectation / Information State Contracts v0.1

**Status:** capture foundation; not model-visible.

## 1. Scientific purpose

The project currently has evidence that causal realized volatility helps estimate future return-distribution scale, but richer endogenous price/volume models have not earned broad incremental OOS value. The next information problem is therefore not "more model capacity"; it is whether variables that represent market beliefs and information surprise add reproducible information.

This document defines how to *observe* those variables without yet claiming they predict anything.

The foundation is deliberately separate from V009. It MUST NOT change V009 training data, artifacts, features, reference, predictions, holdout length, gates, or settlement.

## 2. Latent concepts and observables

The desired latent concepts include:

- **expectation / belief** — observable via consensus estimates, company guidance, surveys or market-implied distributions;
- **uncertainty / disagreement** — observable via estimate dispersion, ranges, option-implied distributions, revisions and disagreement measures;
- **surprise** — derived only when an actual fact becomes available, relative to the latest legitimate pre-event expectation state;
- **attention** — later observable through document/news/search/volume proxies, but not part of this foundation by default;
- **crowding / positioning** — later observable through short interest, flows, options positioning and related sources;
- **scheduled uncertainty** — known future event time/status, independent of unknown direction.

No latent concept is stored as an observed fact unless a source actually provides the corresponding measurement.

## 3. Four immutable observation types

### 3.1 Source observation

Stores evidence provenance:

```text
source_type
source_name
source_ref / canonical_url
published_at
first_seen_at
retrieved_at
available_at
strict_pit
content_sha256
raw_payload
```

A document or API response is evidence, not an economic event.

### 3.2 Scheduled-event observation

Represents what was known about a future event at a particular time:

```text
entity / ticker
event_type
scheduled_for
status
available_at
source_observation_id
```

Reschedules and cancellations APPEND observations. They do not rewrite history.

### 3.3 Expectation observation

A single observed belief statistic:

```text
entity
expectation_type
metric
fiscal_period
statistic
value
provider_as_of
available_at
source_observation_id
```

Examples include analyst EPS consensus mean, company revenue guidance lower bound, survey median or an option-implied statistic. They remain distinct semantic types.

### 3.4 Economic-fact observation

A reported economic quantity that can later resolve an expectation:

```text
entity
fact_type
metric
fiscal_period
actual value
available_at
source_observation_id
```

It is NOT the realized market-return outcome.

## 4. Strict PIT semantics

The architecture's feature gate remains:

```text
feature usable at prediction t
iff legitimate available_at <= t
```

For this live-capture foundation, `strict_pit=1` is intentionally conservative:

1. the system actually observed/retrieved the evidence at that time;
2. timestamps are timezone-aware;
3. the record is never assigned an `available_at` earlier than actual retrieval;
4. a derived observation cannot be available before its source evidence;
5. a strict-PIT derived observation cannot depend on non-strict-PIT source evidence.

Historical backfills may preserve a public publication proxy, but MUST use `strict_pit=0`.

## 5. Append-only revision semantics

Expectations are time series of beliefs, not mutable database cells.

For a conceptual series

```text
(entity, expectation_type, metric, fiscal_period, statistic)
```

new provider values are appended as new observations. Later research may derive causal features such as:

```text
latest expectation as of t
revision_1d / revision_5d / revision_since_prior_event
dispersion level/change
estimate count/change
time_to_event
```

but none of these is model-visible in V001.

## 6. Surprise is derived, never retroactively inserted

For an economic fact `A` first available at time `tau`, let `C(tau-)` be the latest legitimate expectation strictly before the fact became available.

Possible later derived quantities include:

\[
S_{raw}=A-C(\tau^-)
\]

and, when a causal disagreement scale is available,

\[
S_{std}=\frac{A-C(\tau^-)}{D(\tau^-)}.
\]

The sign is NOT mapped to a bullish/bearish return rule. The predictive question remains empirical.

## 7. Isolation from V009

Until a later preregistered experiment:

- capture DB is separate from `market_data.db` and Market Core;
- no V009 fit/refit reads it;
- no V009 seal reads it;
- no V009 settlement reads it;
- no feature builder joins it;
- no result from this capture stream can rescue or alter V009.

## 8. Provider policy

This foundation is provider-agnostic. A provider is not selected merely because it is convenient.

Before adding an adapter, record:

- source authority and licensing/retention constraints;
- whether historical values are true vintage snapshots or today's reconstructed history;
- publication/as-of semantics;
- retrieval rate limits;
- revision behavior;
- entity/metric identifiers;
- coverage universe;
- whether raw payload retention is permitted.

A provider that exposes current consensus but not historical vintages is useful for *prospective capture* but must not be mislabeled as strict historical PIT.

## 9. Future experimental gate

No prediction model is part of this foundation.

A later experiment must choose ONE information increment and preregister:

```text
F0(Y | X, T)
vs
F1(Y | X, E_increment, T)
```

with a capacity-matched no-information control, purged temporal validation, proper distributional scoring and a failure interpretation that permits dropping the information block.
