# Fix: migration 017 predecessor check

## What happened

The real DB already has the physical Event Layer 010 tables, but migration 010
was applied before `schema_migrations` became the canonical ledger.

Therefore:

- 010 exists physically.
- 010 is missing from `schema_migrations`.
- 015 and 016 are correctly registered.
- The original 017 preflight treated the missing ledger row as if 010 had never
  been applied.

That is a false negative.

## Fix

The corrected `apply_migration_017.py`:

1. Still requires 015 and 016 by exact registered identity.
2. If 010 is registered, requires `010|event_layer`.
3. If 010 is not registered, validates the real Event Layer 010 tables and key
   columns.
4. Only after structural validation, backfills:
   `010|event_layer`
5. Applies 017 in the same `BEGIN IMMEDIATE` transaction.
6. Is idempotent.

No Event Layer data is recreated or overwritten.

## Use

Replace:

`database/apply_migration_017.py`

Then run:

```bash
python -m pytest \
  tests/test_event_normalization_contract.py \
  tests/test_database_bootstrap.py \
  tests/test_migration_017_legacy_predecessor.py \
  -q

python database/apply_migration_017.py \
  --db data/database/market_data_v2.db
```

Expected first real-DB result includes:

```text
legacy_010_registry_backfilled: True
status: applied
```

Then verify:

```bash
sqlite3 data/database/market_data_v2.db "
SELECT version, name
FROM schema_migrations
WHERE version IN ('010','015','016','017')
ORDER BY version;
"
```

Expected:

```text
010|event_layer
015|deterministic_event_clustering
016|sec_filing_metadata_versioning
017|event_normalization
```

A second run of `apply_migration_017.py` should succeed and report:

```text
legacy_010_registry_backfilled: False
```
