# Event–Graph Relation Candidates V001

## Purpose

Create the first high-precision structural relationship candidates from the
audited SEC relation corpus.

This package does not mutate `market_data_v2.db` and does not write graph
edges.

The output is a separate processed DB:

```text
data/processed/event_graph_relation_candidates_v001.db
```

## Why V001 is intentionally narrow

The SEC corpus audit found very large keyword counts, especially
supplier/customer language. Keyword frequency is not equivalent to named
economic relationships.

V001 therefore starts with two evidence patterns where the source semantics
are much stronger.

### EX-21

`EX-21*` is used for subsidiaries of the registrant.

V001 extracts organization-like rows ending in recognized legal forms and
creates unresolved/resolved name candidates:

```text
Registrant --parent_of--> Named subsidiary candidate
```

This is still a candidate, not a promoted graph assertion.

### EX-10 / EX-2

Material-contract and transaction exhibits are scanned only near explicit
legal preambles such as:

```text
by and between ...
by and among ...
```

Named legal entities in that narrow region become:

```text
Registrant --contract_party_of--> Named counterparty candidate
```

V001 does not infer acquisition direction, financing direction, supplier
direction, customer direction or market impact from the contract.

## Entity resolution

Automatic resolution is deliberately conservative:

- exact unique existing entity name;
- exact unique asset name through `asset_entities`;
- exact unique ticker through `asset_entities`.

There is no fuzzy auto-resolution, legal-suffix stripping or entity creation.

Unresolved names are expected.

## Evidence contract

Every candidate carries:

- source asset/entity;
- accession;
- raw document SHA-256;
- source URL;
- effective historical availability;
- exact target character span;
- surrounding evidence text;
- extractor/rule version.

Historical corpus rows remain `availability_is_point_in_time=0`.

## Deferred semantics

The following are intentionally not extracted yet:

```text
supplier_of
customer_of
depends_on
partner_of
competitor_of
regulated_by
exposed_to
operates_in
produces
uses
financed_by
```

Those need semantic context and entity resolution rather than keyword rules.

## Run order

Install:

```bash
cd ~/quant_market_ai

unzip -o \
  ~/Downloads/quant_market_ai_event_graph_relation_candidates_v001.zip \
  -d .
```

Tests:

```bash
python -m pytest \
  tests/test_event_graph_relation_candidates_v001.py \
  -q
```

First run only the plan:

```bash
mkdir -p reports/event_graph/relation_candidates_v001

python -m pipeline.event_graph_relation_candidates_v001 \
  --stage plan \
  > reports/event_graph/relation_candidates_v001/plan.json
```

Send `plan.json` before extraction.

After a healthy plan, the next stages are:

```bash
python -m pipeline.event_graph_relation_candidates_v001 \
  --stage extract \
  > reports/event_graph/relation_candidates_v001/extract.json

python -m pipeline.event_graph_relation_candidates_v001 \
  --stage audit \
  > reports/event_graph/relation_candidates_v001/audit.json

python -m pipeline.event_graph_relation_candidates_v001 \
  --stage qa-sample \
  > reports/event_graph/relation_candidates_v001/qa_sample_summary.json
```

The QA sample itself is written to:

```text
reports/event_graph/relation_candidates_v001/qa_sample.json
```

No candidate is allowed to be promoted to
`temporal_relation_assertions_v001` until the QA sample and entity-resolution
behavior are reviewed.
