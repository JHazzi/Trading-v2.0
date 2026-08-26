# Event–Graph Brain Foundation — migration numbering V002

The V001 package selected migration number `019` by comparing against the
public GitHub tree. The user's local database already had:

```text
019 = event_brain_v001
```

The V001 migration guard correctly aborted before executing any foundation DDL.

V002 preserves the existing local `019` and assigns:

```text
020 = event_graph_brain_foundation
```

No scientific contract, event/entity contract, graph semantics, feature,
model, hyperparameter or result was changed. This is a pre-execution schema
numbering correction only.

The V002 migration also hard-fails if local `020` is already occupied by a
different migration.

The helper `tools/fix_event_graph_foundation_migration_number_v002.py` removes
only the obsolete V001 foundation files if they contain the expected Event–
Graph markers; it refuses to delete an unrecognized `019` file.
