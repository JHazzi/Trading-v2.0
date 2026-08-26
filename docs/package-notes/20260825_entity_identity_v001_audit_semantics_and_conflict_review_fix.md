# Entity Identity V001 audit semantics + conflict evidence fix

## Audit correction

The original audit emitted:

```json
"automatic_merge_allowed": true
```

when the database contained zero rows with `auto_merge_allowed != 0`.

That value did **not** mean merges were enabled. The label was semantically
incorrect.

The corrected audit emits:

```json
"automatic_merge_allowed_by_contract": false,
"automatic_merge_candidate_rows": 0,
"automatic_merge_performed": false
```

No identity data are changed.

## Conflict review

Identity V001 produced two pairs with shared-accession co-occurrence. This
overlay adds a read-only report that reconstructs the exact evidence rows for
both names inside every shared accession.

Run:

```bash
python -m pipeline.event_graph_entity_identity_conflict_review_v001
```

Output:

```text
reports/event_graph/entity_identity_v001/conflict_evidence.json
```

No merge, canonical entity, main DB write or graph edge is created.
