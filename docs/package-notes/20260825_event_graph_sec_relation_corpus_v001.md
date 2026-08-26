# Event–Graph SEC Relation Corpus V001

This package converts the already downloaded local SEC corpus into a separate,
reproducible, extraction-ready relation evidence database.

It does **not** create relation candidates or graph edges.

## Why this source

The relation-source audit found no single Tier-A table because the useful SEC
contract is distributed across the schema:

```text
sec_filings / metadata versions
    -> metadata observations
    -> sec_filing_files
    -> raw_source_documents
    -> raw_document_assets
    -> asset_entities
```

The raw payload itself lives at the immutable content-addressed
`raw_source_documents.storage_path`.

## Selected evidence

V001 intentionally prioritizes documents with high structural-relation yield:

- primary 10-K / 10-Q / 8-K / 20-F / 6-K documents;
- Exhibit 21 (`EX-21*`): subsidiaries;
- Exhibit 10 (`EX-10*`): material contracts;
- Exhibit 2 (`EX-2*`): transaction agreements.

Other exhibits/news are deferred until this corpus is audited.

## Clock

SEC `acceptance_datetime` is an EDGAR-assigned timestamp. Existing ingestion
uses it as historical initial availability. However, SEC states that there is
no timestamp indicating when filing content first became available on
sec.gov.

Therefore V001 remains:

```text
strict_historical_pit = false
availability_is_point_in_time = 0
```

The effective corpus clock is the later of the raw-document availability and
the first metadata-observation availability/acceptance clock.

## Integrity

Before text is used:

1. open `storage_path`;
2. decompress gzip when required;
3. SHA-256 the uncompressed payload;
4. require equality with `raw_source_documents.raw_sha256`;
5. normalize HTML/text deterministically;
6. create deterministic overlapping text chunks.

Original raw bytes are never rewritten.

## First run: plan only

```bash
cd ~/quant_market_ai

unzip -o \
  ~/Downloads/quant_market_ai_event_graph_sec_relation_corpus_v001.zip \
  -d .

python -m pytest \
  tests/test_event_graph_sec_relation_corpus_v001.py \
  -q

python -m pipeline.event_graph_sec_relation_corpus_v001 \
  --stage plan \
  > reports/event_graph_sec_relation_corpus_v001_plan.json
```

Send the plan JSON before `--stage build`.

The plan must have zero missing selected storage paths. Narrow asset coverage is
not a data-integrity failure, but it determines whether the next action is
relation extraction on the deep cohort, broader SEC acquisition, or both.

## After a healthy plan

The later stages are:

```bash
python -m pipeline.event_graph_sec_relation_corpus_v001 --stage build
python -m pipeline.event_graph_sec_relation_corpus_v001 --stage audit
```

The output corpus is:

```text
data/processed/event_graph_sec_relation_corpus_v001.db
```

with `corpus_documents` and `corpus_chunks`.

A keyword cue scan is stored only as a yield diagnostic. It is not entity
resolution, not a relation assertion and not a model feature.

The next package after a healthy corpus audit will perform high-precision
relation candidate extraction and entity resolution while preserving the
candidate-vs-promoted-relation boundary.
