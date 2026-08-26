# Event–Graph Entity Registry V002

## Input

Registry V002 supersedes Registry V001 as the preferred future identity input.

It consumes:

```text
data/processed/event_graph_ex21_structured_rows_v001.db
```

rather than regex-derived EX-21 names.

Structured Rows V001 passed its quantitative audit:

- 99/99 documents with data tables;
- 8,111 structured rows;
- 10 seed tickers;
- 99.63% jurisdiction coverage;
- 217 DBA rows;
- 1,517 ownership rows;
- 297 rows with trailing footnote references.

## What a V002 bucket means

A bucket is:

```text
registrant
+ normalized legal name
+ normalized jurisdiction (or explicit MISSING)
```

It is an evidence-backed identity hypothesis **within one registrant scope**.

It is not yet a canonical entity.

This deliberately preserves as distinct:

```text
Johnson & Johnson (Private) Limited — Zimbabwe
Johnson & Johnson Private Limited   — India
```

and it also refuses to globally merge identical names across different
registrants.

That conservative choice is important for future acquisition/divestiture and
multi-source history.

## DBA evidence

A DBA/additional name is stored as alias evidence attached to the bucket:

```text
legal name: Costco Wholesale Canada Ltd.
jurisdiction: Canadian Federal
DBA: Costco Wholesale Canada, Ltd.
```

DBA evidence never changes the bucket key and cannot trigger auto-merge.

## Ownership evidence

Ownership percentage is preserved as evidence associated with the bucket.
It is not used as an identity key because it can change over time.

## Jurisdiction normalization

V002 performs only lexical normalization. It does not map:

```text
Delaware -> United States
England and Wales -> United Kingdom
PRC -> China
```

Those are later reference-data problems.

## Run

```bash
cd ~/quant_market_ai

unzip -o \
  ~/Downloads/quant_market_ai_event_graph_entity_registry_v002.zip \
  -d .

python -m pytest \
  tests/test_event_graph_entity_registry_v002.py \
  -q

mkdir -p reports/event_graph/entity_registry_v002

python -m pipeline.event_graph_entity_registry_v002 \
  --stage plan \
  > reports/event_graph/entity_registry_v002/plan.json
```

Stop after the plan.

After a healthy plan:

```bash
python -m pipeline.event_graph_entity_registry_v002 \
  --stage build \
  > reports/event_graph/entity_registry_v002/build.json

python -m pipeline.event_graph_entity_registry_v002 \
  --stage audit \
  > reports/event_graph/entity_registry_v002/audit.json
```

Canonical entity creation remains blocked.
