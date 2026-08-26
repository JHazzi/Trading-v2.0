# Event–Graph Entity Registry Foundation V001

## Decision from Relation Evidence V002 QA

V002 is split into two scientific outcomes.

### EX-21 branch: supported as registry input

The deterministic QA sample contains overwhelmingly well-formed reported
subsidiary names. One clear column-header false positive,
`Organized or Incorporated`, is filtered in this registry and a patch helper
is included for future V002 reruns.

EX-21 claims remain:

```text
reported_subsidiary_of_registrant
```

They are not promoted to direct `parent_of` graph edges.

### Contract branch: QA failed

`contract_party_mention` contains valid examples but also substantial prose,
role and table-fragment false positives. These claims are retained as
diagnostic evidence but are excluded from Entity Registry V001.

Do not use the contract branch for graph promotion or registry expansion.

## Registry semantics

A registry name record means:

> one exact-normalized legal/entity-like name has repeated evidence in
> historical EX-21 filings.

It does not mean:

> this is already a canonical entity or all spelling variants have been merged.

V001 performs no:

- fuzzy matching;
- suffix stripping for identity;
- alias merging across different normalized names;
- main DB entity creation;
- graph edge creation.

## Run

Install:

```bash
cd ~/quant_market_ai

unzip -o \
  ~/Downloads/quant_market_ai_event_graph_entity_registry_foundation_v001.zip \
  -d .
```

Tests:

```bash
python -m pytest \
  tests/test_event_graph_entity_registry_foundation_v001.py \
  -q
```

Optional future V002 parser patch:

```bash
python tools/patch_relation_evidence_v002_ex21_header_v001.py --check
python tools/patch_relation_evidence_v002_ex21_header_v001.py --apply
```

The registry itself filters the known bad header, so rerunning V002 is not
required merely to build this foundation.

First run only:

```bash
mkdir -p reports/event_graph/entity_registry_v001

python -m pipeline.event_graph_entity_registry_v001 \
  --stage plan \
  > reports/event_graph/entity_registry_v001/plan.json
```

Send `plan.json` before build.

After a healthy plan:

```bash
python -m pipeline.event_graph_entity_registry_v001 \
  --stage build \
  > reports/event_graph/entity_registry_v001/build.json

python -m pipeline.event_graph_entity_registry_v001 \
  --stage audit \
  > reports/event_graph/entity_registry_v001/audit.json
```

Output DB:

```text
data/processed/event_graph_entity_registry_v001.db
```

Next after audit: identity-resolution design using repeated historical name
evidence. Contract-party extraction remains a separate failed/rework branch.
