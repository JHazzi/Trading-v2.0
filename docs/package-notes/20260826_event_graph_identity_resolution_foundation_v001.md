# Event–Graph Identity Resolution Foundation V001

## Why this stage exists

Registry V2 and Identity Conflict QA V001 produced a scientifically useful
separation between identity evidence and identity decisions.

Conflict QA V001:

- 28 same-name / multi-jurisdiction groups;
- 57 buckets;
- 30 jurisdiction pairs;
- 237 evidence rows;
- 8 same-accession pairs;
- 12 temporal-overlap pairs;
- 18 temporal-nonoverlap pairs;
- zero automatic decisions.

It also exposed three missing-jurisdiction buckets:

```text
Consumer Test Entity (TEST PURPOSE ONLY)
International Subsidiaries:
U.S. Subsidiaries:
```

The two `Subsidiaries:` values are section headings, and the first is explicitly
a test placeholder. Together they account for the missing-jurisdiction evidence
rows seen in Structured Rows V001.

The correct response is not `drop all missing jurisdiction`. Future sources may
contain legitimate entities with unknown jurisdiction.

## V001 separates three problems

### 1. Row quality

Rows may be entity observations, headings, test placeholders, or other
non-entity source artifacts.

V001 emits `row_quality_candidates`; it does not delete them.

### 2. Jurisdiction reference

Source labels such as:

```text
Venezuela
Venezuela, Bolivarian Republic of
```

can be candidate labels for one jurisdiction concept.

Likewise:

```text
United States
Washington
```

are related hierarchically but are not equivalent strings or jurisdictions.

The V001 reference file is deliberately small and candidate-only. It is not a
global authoritative jurisdiction database.

### 3. Entity identity

Even if two jurisdiction strings are equivalent, that fact alone is not an
entity-identity verdict.

Likewise, same-accession co-occurrence and temporal non-overlap are evidence,
not automatic split/merge instructions.

## Pair review classes

Every one of the 30 conflict pairs is classified into a review class:

```text
reference_equivalent_candidate
hierarchical_granularity_candidate
same_accession_distinct_or_source_error
temporal_rejurisdiction_or_reporting_change_candidate
unresolved_overlap_distinct_jurisdiction
```

No identity verdict is written.

## Next order of operations

If QA is healthy:

1. confirm the three row-quality exclusions;
2. create Structured Rows V002 with explicit non-entity rejection provenance;
3. rebuild Entity Registry V2 from the cleaned structured rows;
4. rebuild conflict QA;
5. version a broader jurisdiction reference layer;
6. only then design cross-bucket canonical identity resolution.

Canonical entities and graph edges remain blocked.

## Run

```bash
cd ~/quant_market_ai

unzip -o \
  ~/Downloads/quant_market_ai_event_graph_identity_resolution_foundation_v001.zip \
  -d .

python -m pytest \
  tests/test_event_graph_identity_resolution_foundation_v001.py \
  -q

mkdir -p reports/event_graph/identity_resolution_foundation_v001

python -m pipeline.event_graph_identity_resolution_foundation_v001 \
  --stage plan \
  > reports/event_graph/identity_resolution_foundation_v001/plan.json
```

Stop after `plan`.

After a healthy plan:

```bash
python -m pipeline.event_graph_identity_resolution_foundation_v001 \
  --stage build \
  > reports/event_graph/identity_resolution_foundation_v001/build.json

python -m pipeline.event_graph_identity_resolution_foundation_v001 \
  --stage audit \
  > reports/event_graph/identity_resolution_foundation_v001/audit.json
```

The build also writes the complete QA report:

```text
reports/event_graph/identity_resolution_foundation_v001/
  qa_all_pairs_and_quality_candidates.json
```
