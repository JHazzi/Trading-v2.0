# Event–Graph Relation Source Audit V001

## Purpose

The local repository is ahead of the public GitHub tree. Before implementing
historical relation extraction, inspect the actual local database rather than
assuming public table names/contracts.

This stage is strictly read-only.

It does not:

- add a migration;
- create entities or relations;
- modify events;
- call an LLM;
- train a model.

## What it inventories

Every local SQLite table is inspected for:

- row count;
- schema;
- time / availability fields;
- point-in-time / revision / version fields;
- entity/asset/document identifiers;
- raw/full-text or payload fields;
- source references;
- foreign-key topology.

Likely relation-evidence sources are ranked into readiness tiers.

Tier A means the table structurally appears to provide most of:

```text
causal/availability clock
+ source reference
+ entity/asset resolvability
+ full text/payload
+ provenance/version information
```

A Tier-A rank is not permission to extract. Exact source semantics must still
be reviewed.

## Run

```bash
cd ~/quant_market_ai

unzip -o \
  ~/Downloads/quant_market_ai_event_graph_relation_source_audit_v001.zip \
  -d .

python -m pytest \
  tests/test_event_graph_relation_source_audit_v001.py \
  -q

python -m pipeline.event_graph_relation_source_audit_v001
```

The pipeline writes:

```text
reports/event_graph_relation_source_audit_v001.json
```

Send that JSON before implementing relation extraction.

## Scientific gate

The next package must be designed against the local Tier-A/Tier-B sources
actually discovered by this audit.

Do not create graph edges from current-world company relationships and apply
them retroactively. Historical graph membership must be gated by evidence
available at the prediction time.
