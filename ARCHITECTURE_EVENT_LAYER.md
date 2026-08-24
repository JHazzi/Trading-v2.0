# Event / News Layer — Quant Market AI

This package implements the next architectural layer after Market Brain V002.
It follows the repository architecture: raw news -> event cluster -> event -> event features -> event adjustment.

## Core principle

A news document is evidence, not a market shock by itself.

100 articles repeating the same announcement should become:

```text
100 news_documents
        |
        v
   1 event_cluster
        |
        v
      1 event
        |
        +---- many evidence records
```

The event layer must be causal: a prediction at time `t` can only use information whose `available_at <= t`.

## Source-document foundation

The implemented source boundary is:

```text
official response bytes
        -> raw_source_documents (hash, path, availability, retrieval)
        -> normalized source record (for example sec_filings)
        -> asset link
        -> later clustering and event state
```

The original payload is immutable and compressed outside SQLite. A normalized
filing record is marked `parsed` and points to the exact official response
through `parent_raw_document_id`.

Ingestion records source facts and timing only. It does not assign importance,
sentiment, direction, reliability or decay. Those quantities belong to later,
versioned learning and evaluation stages.

## Event representation

An event contains:

- semantic identity/type;
- temporal lifecycle;
- involved entities/assets;
- scope: company / industry / market / macro / cross-asset;
- expected-vs-realized context;
- evidence from multiple sources;
- learned reliability and novelty;
- later observed market reaction.

No economic impact is hardcoded.

## Event effect

The Event Brain should eventually estimate a change to the *distribution*, not a single return:

```text
Delta event = {
    delta_expected_return,
    delta_uncertainty,
    delta_tail_risk,
    delta_regime_probability
}
```

Conceptually:

```text
P(R[t:t+T] | X_t, E_t, G_t, T)
```

where `E_t` contains only events available at `t`.

## Temporal lifecycle

An event can have:

- scheduled_at: known future time, when applicable;
- first_seen_at: first observed in the ingestion system;
- available_at: first time the information could legitimately be used by the model;
- event_time: time the underlying event happened;
- effective_until: optional learned validity boundary;
- resolved_at: when uncertainty was resolved.

`available_at` is the anti-leakage timestamp. It is the one that must gate features.

## Future behavior

The event effect must not use a universal exponential decay. Persistence, amplification, sign changes and uncertainty effects are learned from observed outcomes.

## Learning loop

```text
news -> cluster -> event -> features -> prediction
                                  |
                                  v
                              real outcome
                                  |
                                  v
                         event reaction dataset
                                  |
                                  v
                         event model candidate
```

The first event model should not replace Market Brain V002. It should be evaluated as an incremental information source.
