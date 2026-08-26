# Event–Graph Relation Evidence V002

V001 successfully proved the corpus/extraction plumbing and preserved all
scientific guards, but its QA failed the intended high-precision gate.

## What QA revealed

### Contract candidates

V001 sometimes produced malformed names from:

- table-of-contents text;
- generic roles such as Company/Acquiror;
- jurisdiction descriptions;
- addresses;
- multiple parties fused into one name.

More importantly, the registrant filing an exhibit is not necessarily a legal
party to that exhibit. Therefore:

```text
registrant -> contract_party_of -> extracted name
```

is not a defensible generic representation.

V002 instead extracts a **document-level party set**. No pairwise contract
edge is created.

### EX-21

EX-21 is strong structural evidence but V001 overclaimed direct `parent_of`.
An exhibit lists reported subsidiaries of the registrant and can contain
direct/indirect ownership structures or additional disclosure nuances.

V002 stores:

```text
reported_subsidiary_of_registrant
```

as an evidence claim. It is not yet an edge.

The name parser also prefers the longest legal suffix and rejects known
headers/broken table fragments.

## No graph promotion

V002 output:

```text
data/processed/event_graph_relation_evidence_v002.db
```

contains evidence claims and contract party sets only.

It does not write:

- graph assertions;
- pairwise contract relations;
- parent_of edges;
- market direction;
- market weights.

## Run

```bash
cd ~/quant_market_ai

unzip -o \
  ~/Downloads/quant_market_ai_event_graph_relation_evidence_v002.zip \
  -d .

python -m pytest \
  tests/test_event_graph_relation_evidence_v002.py \
  -q

mkdir -p reports/event_graph/relation_evidence_v002

python -m pipeline.event_graph_relation_evidence_v002 \
  --stage plan \
  > reports/event_graph/relation_evidence_v002/plan.json
```

Stop after the plan and review it.

After a healthy plan:

```bash
python -m pipeline.event_graph_relation_evidence_v002 \
  --stage extract \
  > reports/event_graph/relation_evidence_v002/extract.json

python -m pipeline.event_graph_relation_evidence_v002 \
  --stage audit \
  > reports/event_graph/relation_evidence_v002/audit.json

python -m pipeline.event_graph_relation_evidence_v002 \
  --stage qa-sample \
  > reports/event_graph/relation_evidence_v002/qa_sample_summary.json
```

The QA sample is written to:

```text
reports/event_graph/relation_evidence_v002/qa_sample.json
```

Do not begin supplier/customer semantic extraction until this QA passes the
precision gate.
