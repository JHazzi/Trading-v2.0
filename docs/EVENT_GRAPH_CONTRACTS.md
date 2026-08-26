# Event–Graph Brain Contracts

Status: foundation contract, before graph-model training.

Authority remains `ARCHITECTURE.md` and `ARCHITECTURE_EVENT_LAYER.md`.

## 1. Purpose

The Market Brain is a base prior, not the entire explanation of an asset's
future. Event–Graph Brain adds the two missing conditioning objects:

```text
P(R[t:t+T] | X_t, E_t, G_t, T)
```

- `X_t`: market / asset state;
- `E_t`: events known by `t`;
- `G_t`: relationships supported by evidence available by `t`.

The foundation deliberately does not claim that an index, ETF, news article or
graph edge mechanically causes an asset return.

## 2. Layer separation

```text
raw evidence
    ↓
event cluster
    ↓
canonical/direct event state
    ↓
entity candidates
    ↓ resolution / promotion
event ↔ entity factual links

source evidence
    ↓
relation candidate
    ↓ validation / promotion
temporal structural relation

event + entity + G_t
    ↓
exposure candidates
    ↓
future reaction model
```

An extraction candidate is never model-visible simply because an NLP/LLM
assigned high confidence.

## 3. Entity semantics

A listed asset is not automatically identical to its issuer.

The foundation creates `listed_asset_proxy` entities only for assets that do
not yet have a mapping. A proxy means:

> this graph node corresponds to this listed asset while issuer/entity
> resolution is incomplete.

Later resolution can connect or replace proxies with company, person, product,
regulator, country, commodity and other entity nodes.

## 4. Temporal graph

A structural relation has two clocks:

1. real-world validity (`valid_from`, `valid_to`) when known;
2. evidence availability (`evidence_available_at`).

The model-visible graph is gated by evidence availability:

```text
relation ∈ G_t
only if
evidence_available_at <= t
```

A later correction/retraction creates a new observation. It does not rewrite
history.

## 5. Structural graph first

Foundation relations include relationships such as supplier/customer,
competitor, parent/subsidiary, contract party, regulator, geographic/economic
exposure, product usage/dependency and financing.

The structural graph is not assigned:

- positive/negative market direction;
- predictive weight;
- universal confidence;
- universal persistence;
- causal strength.

Those are future learned quantities evaluated against outcomes.

Statistical and learned graphs remain separate and deferred.

## 6. Propagation means candidate discovery

For an event linked to entity A, one-hop traversal may nominate connected
entity B and its listed asset as *potentially exposed*.

It does **not** say whether B should rise or fall.

```text
event(A)
  → structural edge A—B
  → asset(B) is an exposure candidate
```

The path records relation type and whether it was traversed in the stored edge
direction or reverse direction.

Foundation maximum: one graph hop.

No GNN or learned graph attention is allowed before a simple temporal graph
demonstrates incremental value.

## 7. Evaluation ladder

The experiments must be nested.

### D1 — direct event value

```text
V004 + direct event
vs
V004
```

Question: does the event itself add OOS information?

### E0 — graph exposure validation

Before prediction, test graph candidate quality with negative controls:

- matched unconnected assets;
- same-sector but unconnected assets;
- quiet days;
- pre-event windows;
- future relation evidence must be invisible.

### E1 — graph incremental value

```text
V004 + direct event + graph
vs
V004 + direct event
```

Question: does graph context add information beyond knowing the event?

A graph model is not successful merely because it predicts events better than
V004. It must beat the direct-event control.

## 8. Outcome formulation

Raw realized return remains observational ground truth.

For Event–Graph learning, the preferred incremental target is eventually an
abnormal outcome relative to the frozen base prior at a compatible prediction
origin:

```text
abnormal outcome
=
realized asset outcome
-
frozen V004 base prediction
```

This target must not be created until daily event clocks and V004 prediction
origins are explicitly aligned and audited.

## 9. What is deliberately deferred

- new broad news providers;
- LLM semantic extraction;
- statistical correlation graph;
- learned graph;
- multi-hop propagation beyond one hop;
- GNN / graph attention;
- hardcoded event direction or decay;
- distributional Event Brain heads;
- production trading.

The next work after this foundation is data acquisition/extraction of
historical structural relationships with defensible temporal evidence.
