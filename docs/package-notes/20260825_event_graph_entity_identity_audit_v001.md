# Event–Graph Entity Identity Audit V001

## Why this stage exists

Entity Registry V001 produced 1,201 exact-normalized EX-21 legal-name records
from 5,820 accepted evidence rows, with no canonical entities or graph edges.

The next problem is not graph propagation. It is identity.

Examples such as:

```text
Acme, Inc.
Acme Incorporated
```

may be formatting aliases, while:

```text
Acme Inc.
Acme LLC
```

must not be collapsed merely because the business-name stem matches.

## V001 identity hypothesis

Generate conservative cross-name identity candidates only when:

1. names map to the same punctuation/legal-form-preserving identity key; and
2. the names share at least one historical registrant.

Legal forms are canonicalized to families such as `INC`, `LLC`, `LTD`,
`SA_DE_CV`, but they are never stripped.

There is no fuzzy matching, embedding similarity or LLM identity decision.

## Important negative evidence

If two candidate names occur in the same accession, V001 records:

```text
same_accession_cooccurrence = true
```

as a conflict flag.

A candidate pair is never auto-merged, even without that flag.

## Output

Separate DB:

```text
data/processed/event_graph_entity_identity_candidates_v001.db
```

It contains:

- name profiles;
- identity candidate pairs;
- same-accession conflict flags;
- deterministic QA samples.

It creates no canonical entity and modifies no main DB table.

## Run

```bash
cd ~/quant_market_ai

unzip -o \
  ~/Downloads/quant_market_ai_event_graph_entity_identity_audit_v001.zip \
  -d .

python -m pytest \
  tests/test_event_graph_entity_identity_audit_v001.py \
  -q

mkdir -p reports/event_graph/entity_identity_v001

python -m pipeline.event_graph_entity_identity_v001 \
  --stage plan \
  > reports/event_graph/entity_identity_v001/plan.json
```

Send `plan.json` before build.

After a healthy plan:

```bash
python -m pipeline.event_graph_entity_identity_v001 \
  --stage build \
  > reports/event_graph/entity_identity_v001/build.json

python -m pipeline.event_graph_entity_identity_v001 \
  --stage audit \
  > reports/event_graph/entity_identity_v001/audit.json

python -m pipeline.event_graph_entity_identity_v001 \
  --stage qa-sample \
  > reports/event_graph/entity_identity_v001/qa_sample_summary.json
```

The sample is written to:

```text
reports/event_graph/entity_identity_v001/qa_sample.json
```

No candidate may become a canonical entity or alias until QA is reviewed.
