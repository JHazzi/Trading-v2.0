# Event / News Layer — Canonical Architecture v0.2

**Status:** canonical event-layer contract  
**Last major review:** 2026-08-25

## 1. Core principle

A source document is **evidence**, not a market shock.

```text
many source documents
        ↓
deterministic/causal evidence clustering
        ↓
normalized economic event identities
        ↓
event observations over time
        ↓
causal Event State E(t)
        ↓
incremental information test vs Market Brain
```

`100 articles` repeating an announcement are not `100 independent events`.

Conversely, one SEC filing can encode several distinct economic events.

## 2. Implemented SEC research pipeline

The current deep research path is:

```text
SEC metadata/version observations
        ↓
immutable raw filing documents/exhibits
        ↓
deterministic clustering
        ↓
stable normalized event identity
        ↓
event observations / evidence semantics
        ↓
Event State snapshots
        ↓
1/3/5/10-session reaction labels
        ↓
walk-forward Event Brain benchmark
```

Current research versions:

- normalization: `sec_event_normalizer_v0031_deep_raw_lineage`;
- event state: `event_state_v0031_deep`;
- labels: `event_reaction_daily_v0031_deep`.

These versions represent a historical research reconstruction, not strict live PIT capture.


<!-- EVENT_T0_V001_START -->
### Source authority is not first disclosure

For an economic event, the authoritative source and the first public source
may be different.

Define conceptually:

```text
first_public_at(event)
    = earliest legitimate public availability among evidence items
      that existed at that moment
```

Do **not** assume:

```text
SEC accepted_at == first_public_at == event_time
```

Examples include earnings releases published through Investor Relations or
a press-release wire before the corresponding 8-K, or a reported/rumored
transaction that precedes official confirmation.

The Event State must evolve through time. Later SEC evidence can improve
confirmation, detail and provenance without retroactively moving information
into an earlier state.

For intraday research, a model may react only to the evidence that was
actually/publicly available by the prediction timestamp.
<!-- EVENT_T0_V001_END -->

## 3. Temporal lifecycle

Keep distinct:

- `event_time`: when the underlying economic event occurred;
- `scheduled_at`: known future event time, if applicable;
- `published_at` / acceptance time: source publication;
- `first_seen_at`: first observed by our ingestion system;
- `retrieved_at` / observation time: when bytes/metadata were actually retrieved;
- `available_at`: earliest legitimate model-availability boundary under the stated data contract;
- `effective_until`: optional learned validity boundary;
- `resolved_at`: when uncertainty is resolved.

`available_at` is the feature gate.

Historical backfill rule:

> A historical public-availability proxy may be used only when the row remains explicitly non-strict-PIT and the actual later retrieval timestamp is retained separately.

Never rewrite a 2026 retrieval as a 2018 observation to improve backtest coverage.

## 4. Identity and lineage

Stable economic identity must not depend on rerun timestamp.

For current SEC normalized events the stable identity is based on SEC accession/item semantics.

Reruns may add new observations/versioned lineage, but must not silently create duplicate economic identities.

State/label feature versions must prevent mixing partially reconstructed pilot evidence with a later complete deep corpus.

## 5. Evidence semantics

Normalization may represent factual/taxonomic semantics such as filing form, SEC item, epistemic type, scope and direct entity/asset mapping.

Normalization must **not** hardcode:

- bullish/bearish direction;
- economic importance;
- source predictive reliability;
- expected persistence/decay;
- relation propagation strength.

## 6. Current Event State

The current research features are intentionally simple and mostly structural/factual:

- event type/scope;
- evidence counts;
- cluster/source counts;
- event age;
- temporal/scheduled flags;
- evidence/source signatures.

This representation is enough for a minimal incremental-information experiment, but it is not a semantic understanding of earnings, guidance or expectations.

## 7. Event Brain target architecture

Current scalar benchmark:

```text
return_pct
```

is a bounded research test, not the end-state.

Target comparison:

\[
F_0(Y\mid X_t,T)
\quad \text{vs} \quad
F_1(Y\mid X_t,E_t,G_t,T)
\]

An event can improve prediction of:

- median/expected return;
- quantile width;
- downside/upside tails;
- realized path volatility;
- MFE;
- MAE;
- regime transition probability;
- persistence/decay.

An event that does not move median return can still be highly informative about uncertainty or tails.

## 8. Expectations and surprise

For many events the economically meaningful variable is not the raw fact but the difference between reality and what the market expected.

Future representation should support:

```text
actual
expectation
prior guidance
revised guidance
consensus
novelty
surprise = actual - expectation
```

Do not hardcode the sign of surprise into price direction.

## 9. Reliability is multidimensional

Do not collapse these into one manual score:

- factual reliability;
- information novelty;
- predictive usefulness;
- economic materiality;
- market impact;
- persistence;
- corroboration.

These should become learned/contextual quantities when data supports them.

## 10. Graph separation

Direct event→asset links are different from graph propagation.

First establish local event information value. Only then model:

```text
event on entity A
     ↓
structural/statistical/learned relations
     ↓
possible effect on B/C/sector/market
```

No co-occurrence-as-causality shortcut.

## 11. Current scientific interpretation

The current deep SEC corpus is large enough for serious research compared with the pilot, but:

- it is a 10-current-company cohort and therefore not survivorship-free;
- historical SEC evidence is reconstructed with PIT=0;
- multiple events from one accession can be dependent;
- multi-session labels overlap in calendar time;
- the daily Market Brain remains weak;
- event features do not yet include full text/numeric surprise/expectations.

Therefore current Event Brain results measure a narrow question:

> Does factual SEC event-state metadata add incremental predictive information to the current market-state representation?

They do not establish trading profitability.

## 12. Next event-layer work

Do **not** add more SEC documents immediately.

Ordered next work:

1. robustness/falsification of the H10 candidate;
2. stronger Market Brain;
3. distributional Market Brain;
4. distributional Event Brain using the existing event corpus;
5. richer event semantics/expectations;
6. additional sources;
7. learned reliability/novelty;
8. graph propagation.

See `docs/ROADMAP.md`.
