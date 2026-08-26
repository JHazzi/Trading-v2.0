# Event–Graph Identity Conflict QA V001

## Why this stage exists

Registry V002 successfully materialized:

- 8,111 evidence rows;
- 1,650 registrant-scoped identity evidence buckets;
- 217 DBA alias-evidence rows;
- 28 same-name / multiple-jurisdiction groups;
- 3 missing-jurisdiction buckets.

The next mistake to avoid is treating jurisdiction normalization and entity
identity as the same problem.

## This stage is read-only with respect to identity

For every one of the 28 conflict groups, V001 records:

- every bucket;
- every historical evidence row;
- first/last evidence time;
- accessions;
- jurisdiction;
- DBA;
- ownership;
- footnote references;
- whether bucket evidence ranges overlap;
- whether the two jurisdictions appear in the same accession;
- whether one bucket ends before the other begins.

These are evidence signals only.

V001 makes **no automatic merge or split decision**.

## Examples of questions the report can answer

```text
Korea
vs
Korea, Republic of
```

Did both appear in the same filing? Is one simply a later rendering?

```text
Delaware
vs
Massachusetts
```

Do the evidence ranges overlap? Is there a clean temporal transition?

```text
India
vs
Saudi Arabia
```

Were both reported simultaneously? If so, that is strong evidence that the
same string may identify distinct legal entities or that one row needs review.

## Missing jurisdiction

The three missing-jurisdiction buckets are included in full so the next step
can determine whether the raw table or another source can recover the missing
field.

## Run

```bash
cd ~/quant_market_ai

unzip -o \
  ~/Downloads/quant_market_ai_event_graph_identity_conflict_qa_v001.zip \
  -d .

python -m pytest \
  tests/test_event_graph_identity_conflict_qa_v001.py \
  -q

mkdir -p reports/event_graph/identity_conflict_qa_v001

python -m pipeline.event_graph_identity_conflict_qa_v001 \
  --stage plan \
  > reports/event_graph/identity_conflict_qa_v001/plan.json
```

Stop after the plan.

After approval:

```bash
python -m pipeline.event_graph_identity_conflict_qa_v001 \
  --stage build \
  > reports/event_graph/identity_conflict_qa_v001/build.json

python -m pipeline.event_graph_identity_conflict_qa_v001 \
  --stage audit \
  > reports/event_graph/identity_conflict_qa_v001/audit.json
```

The complete evidence report is written to:

```text
reports/event_graph/identity_conflict_qa_v001/conflict_evidence.json
```

No canonical entities or graph edges are created.
