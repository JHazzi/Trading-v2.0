# Event–Graph EX-21 Structured Rows V001

## Findings from Structure Audit V001

The apparent 54.5% structured-table coverage was misleading.

Review of all 99 document-detail records found three systematic classes:

1. **50 documents** already have genuinely recognized explicit schemas.
2. **16 documents** have explicit legal-name/jurisdiction headers but the
   vocabulary was missing from V001:
   - Chevron: `State, Province or Country in Which Organized`
   - JPMorgan: `Organized Under The Laws Of`, with `Name` prefixed by a date.
3. **33 documents** have a blank legal-name header but regular rows and explicit
   jurisdiction and/or ownership columns:
   - Apple;
   - Lilly;
   - ExxonMobil.

These classes cover all 99 documents.

Structure Audit V001 also had four XOM false positives where a footnote table
was classified as a structured table because long prose contained words such
as `subsidiary` and `percent`.

## Span finding

The `rowspan/colspan` warning was mostly formatting noise.

Across the audited document details, 129 of 130 tables with any span metadata
had a span on every physical cell, usually the same `colspan=3`. This is a
layout convention, not evidence of additional logical columns.

V001 therefore keeps physical `<td>/<th>` cells as the primary grid and does
not expand uniform spans.

## Extraction strategy

The extractor:

- verifies raw SHA-256;
- rejects footnote/narrative tables;
- recognizes a broader, anchored header vocabulary;
- infers an unlabeled legal-name column only when another semantic column
  (`jurisdiction` or `ownership`) is explicit and multiple subsequent rows
  support the inference;
- preserves spacer columns without treating them as semantic fields;
- inherits schema only to continuation tables in the same document with the
  same physical arity;
- separates trailing numeric footnote markers such as `(4) (5)` from the legal
  name;
- preserves raw jurisdiction, location, DBA and ownership;
- creates no canonical entity and no graph relation.

## Why this supersedes Registry V1 as future identity input

The earlier regex-based EX-21 name extraction required entity-like/legal-form
patterns and therefore could miss legitimate names such as:

```text
Apple Sales International
Apple Operations International
Lilly Cayman Holdings
ExxonMobil Canada Properties
```

Structured table semantics can recover these names without inventing a legal
suffix.

Entity Registry V1 and Identity V1 remain useful research artifacts, but
future identity work should be rebuilt from the structured-row corpus if QA
passes.

## Run

```bash
cd ~/quant_market_ai

unzip -o \
  ~/Downloads/quant_market_ai_event_graph_ex21_structured_rows_v001.zip \
  -d .

python -m pytest \
  tests/test_event_graph_ex21_structured_rows_v001.py \
  -q

mkdir -p reports/event_graph/ex21_structured_rows_v001

python -m pipeline.event_graph_ex21_structured_rows_v001 \
  --stage plan \
  > reports/event_graph/ex21_structured_rows_v001/plan.json
```

Stop after the plan and inspect it.

After a healthy plan:

```bash
python -m pipeline.event_graph_ex21_structured_rows_v001 \
  --stage build \
  > reports/event_graph/ex21_structured_rows_v001/build.json

python -m pipeline.event_graph_ex21_structured_rows_v001 \
  --stage audit \
  > reports/event_graph/ex21_structured_rows_v001/audit.json

python -m pipeline.event_graph_ex21_structured_rows_v001 \
  --stage qa-sample \
  > reports/event_graph/ex21_structured_rows_v001/qa_sample_summary.json
```

QA rows are written to:

```text
reports/event_graph/ex21_structured_rows_v001/qa_sample.json
```

Canonical entity creation remains blocked until QA verifies row/column
alignment across all layout families.
