# Event–Graph Brain Foundation V001

## Why now

The price-only and market-context research has produced a useful frozen base
prior, but repeated additions of aggregate market proxies have not established
robust absolute skill. The repository's original architecture conditions on
market state, events and graph state, so the next work resumes phases D/E
instead of continuing marginal Market State expansion.

This package trains no predictive model.

All foundation bridge/promotion paths are designed to be idempotent: rerunning the same direct-event bridge preserves link provenance, and promoting the same relation evidence twice does not create a duplicate temporal observation.

## What it adds

- migration 020 for causal event/entity/graph contracts;
- asset-specific proxy entities for unmapped active equities;
- bridge from existing normalized direct events to entity links;
- relation candidate vs validated assertion separation;
- temporal relation observations with explicit availability;
- `G_t` reconstruction;
- one-hop exposure candidate generation with edge orientation;
- audit and research documentation.

## Critical semantics

A graph edge means:

```text
there is evidence for this relationship
```

It does not mean:

```text
positive impact
negative impact
predictive weight
causal magnitude
```

Propagation means candidate discovery, not return prediction.

## Install

```bash
cd ~/quant_market_ai

unzip -o \
  ~/Downloads/quant_market_ai_event_graph_brain_foundation_v001.zip \
  -d .
```

## Tests

```bash
python -m pytest \
  tests/test_event_graph_brain_foundation_v001.py \
  -q
```

## Documentation

```bash
python tools/patch_event_graph_foundation_docs_v001.py --check
python tools/patch_event_graph_foundation_docs_v001.py --apply
```

Commit this foundation before relation extraction or model results.

## Stage 1 — migration

```bash
python -m pipeline.event_graph_brain_foundation_v001 \
  --stage migrate
```

## Stage 2 — active equity entity coverage

```bash
python -m pipeline.event_graph_brain_foundation_v001 \
  --stage seed-asset-entities
```

Existing asset→entity mappings are preserved. Missing mappings receive
`listed_asset_proxy` nodes. These are explicitly *not* asserted to be resolved
issuer identities.

## Stage 3 — bridge existing direct events

```bash
python -m pipeline.event_graph_brain_foundation_v001 \
  --stage bridge-direct-events
```

This uses existing `event_state_v002` normalized event states only. It creates
no supplier/customer/etc. relation and no impact inference.

## Stage 4 — foundation audit

```bash
python -m pipeline.event_graph_brain_foundation_v001 \
  --stage audit \
  > reports/event_graph_brain_foundation_v001_audit.json
```

Send that JSON before building any structural-relation ingestor.

An empty structural graph is expected at this stage and appears as REVIEW,
not as scientific failure. The next gate is historical structural-relation
evidence with defensible availability.

## After the audit

Do not train Event–Graph Brain yet.

The next package should implement historical structural-relation acquisition /
extraction, beginning with source documents that already have strong temporal
provenance. Candidate extraction must remain separate from promoted graph
relations.
