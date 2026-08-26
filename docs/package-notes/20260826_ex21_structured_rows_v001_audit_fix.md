# EX-21 Structured Rows V001 — audit aggregate fix

## Failure

`build` completed successfully, but `audit` failed at:

```python
doc_schema = dict(c.execute(...).fetchone())
```

The SQL query returns one row containing six scalar aggregate values:

```text
(explicit_docs, implicit_docs, inherited_docs,
 footnote_tables, uniform_span_tables, unsupported_tables)
```

That tuple is not a sequence of `(key, value)` pairs, so `dict(...)` raises:

```text
TypeError: cannot convert dictionary update sequence element #0 to a sequence
```

## Fix

The audit now keeps the aggregate row as a six-element tuple, validates its
width, and indexes it explicitly.

The extraction database is unchanged. There is no reason to rerun `build` or
`qa-sample`; rerun only the audit after installing this overlay.

## Commands

```bash
cd ~/quant_market_ai

unzip -o \
  ~/Downloads/qmai_ex21_structured_rows_v001_audit_fix.zip \
  -d .

python -m pytest \
  tests/test_ex21_structured_rows_audit_v001_fix.py \
  -q

python -m pipeline.event_graph_ex21_structured_rows_v001 \
  --stage audit \
  > reports/event_graph/ex21_structured_rows_v001/audit_fixed.json
```
