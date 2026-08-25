# 017 — Event Normalization

This is the first post-infrastructure semantic layer.

It deliberately keeps:
- occurrence time separate from information availability;
- repeated documents separate from economic events;
- facts/opinions/forecasts/rumors as evidence semantics;
- direct event links separate from graph propagation;
- economic impact/reliability/persistence out of normalization.

## Install

Copy files into the repository preserving paths.

Then:

```bash
python tools/integrate_017_bootstrap.py
python -m pytest tests/test_event_normalization_contract.py tests/test_database_bootstrap.py -q
python -m py_compile database/apply_migration_017.py tools/integrate_017_bootstrap.py
```

Check DB chain:

```bash
sqlite3 data/database/market_data_v2.db "
SELECT version, name
FROM schema_migrations
WHERE version BETWEEN '015' AND '017'
ORDER BY version;
"
```

If 015 and 016 exist and 017 does not:

```bash
python database/apply_migration_017.py \
  --db data/database/market_data_v2.db
```

Then verify:

```bash
sqlite3 data/database/market_data_v2.db "
SELECT version, name
FROM schema_migrations
WHERE version='017';

SELECT name
FROM sqlite_master
WHERE type='table'
  AND name IN (
    'event_normalization_configs',
    'event_normalization_runs',
    'normalized_event_identities',
    'normalized_event_versions',
    'normalized_event_observations',
    'event_cluster_event_links',
    'event_evidence_semantics',
    'normalized_event_entity_links',
    'normalized_event_asset_links'
  )
ORDER BY name;
"
```

Do not populate impact, direction, source reliability, importance or decay here.

Next implementation after this schema passes:
`SEC v0.1 normalizer -> event observations -> daily reaction labels -> Event Brain v0.1`.
