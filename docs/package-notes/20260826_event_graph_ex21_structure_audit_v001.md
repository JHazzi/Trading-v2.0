# Event–Graph EX-21 Structure Audit V001

## Why this stage exists

Entity Identity V001 exposed two same-accession cases with opposite meanings.

### Costco

The EX-21 evidence places:

```text
Costco Wholesale Canada Ltd.
Canadian Federal
Costco Wholesale Canada, Ltd.
```

inside a filing whose table includes a doing-business-name column. This is
consistent with one legal subsidiary plus an alternate/DBA name.

### Johnson & Johnson

The same EX-21 separately reports:

```text
Johnson & Johnson (Private) Limited — Zimbabwe
Johnson & Johnson Private Limited   — India
```

These are distinct legal entities despite their near-identical strings.

Therefore string normalization cannot be the deciding identity mechanism.

## Objective

Audit the **raw EX-21 HTML structure** and measure how often we can recover
table columns such as:

- legal subsidiary name;
- jurisdiction/place of incorporation;
- DBA/additional names;
- ownership percentage.

This package is read-only. It verifies raw SHA-256 before parsing.

It creates no entities, identity merges, relations or graph edges.

## Why raw HTML instead of normalized text

The normalized corpus text intentionally preserved readable content but lost
some table-column semantics. Identity resolution now requires those semantics.

The raw source payload remains authoritative and content-addressed, so V001
returns to the raw document and audits `<table>/<tr>/<td>/<th>` boundaries,
including `rowspan` and `colspan`.

## Run

```bash
cd ~/quant_market_ai

unzip -o \
  ~/Downloads/quant_market_ai_event_graph_ex21_structure_audit_v001.zip \
  -d .

python -m pytest \
  tests/test_event_graph_ex21_structure_audit_v001.py \
  -q

mkdir -p reports/event_graph/ex21_structure_v001

python -m pipeline.event_graph_ex21_structure_audit_v001 \
  --stage plan \
  > reports/event_graph/ex21_structure_v001/plan.json

python -m pipeline.event_graph_ex21_structure_audit_v001 \
  --stage audit \
  > reports/event_graph/ex21_structure_v001/audit_stdout.json
```

The audit also writes:

```text
reports/event_graph/ex21_structure_v001/audit.json
reports/event_graph/ex21_structure_v001/document_details.json
```

Send `plan.json` and `audit.json`.

## Decision rule

If a large majority of EX-21 documents expose recoverable name/jurisdiction
table structure, the next package will build structured historical rows.

If coverage is poor, we will not force a generic parser. We will stratify
documents by layout family and build source-format-specific extractors.

Canonical entity creation remains blocked until jurisdiction/role evidence is
available.
