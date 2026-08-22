# Legacy → V2 migration policy

## What is migrated as current data

- `universo_tickers` → `assets`
- `precios` → `price_bars`, with `interval='1m'` and `source='legacy:yfinance'`
- `noticias` → `news_documents` + `news_assets`
- `id_evento` → `events` + `event_news` + `event_assets`
- `relaciones_organicas` → `entities` + `entity_relations` using `learned_relation`
- `macro_diario` → `macro_observations`

## What is archived instead of treated as ground truth

- `correlaciones` → `legacy_correlations`
- `vectores_estado` → `legacy_state_vectors`
- `paper_trading` → `legacy_paper_trading`

The legacy `MFE 60m` target is preserved for reproducibility but is **not** used as the new canonical target.

## Why

The audit showed:

- 503 assets
- 1,111,944 one-minute price rows
- 62,671 news rows
- 33,837 organic relations
- 672 legacy correlation rows
- 379 legacy state vectors
- 1 macro day
- 1 paper-trading row

The event/target tables are therefore sparse compared with the raw market/news history. They are valuable evidence, but not a sufficient foundation for the new forecasting target.

## Safety

The source DB is read-only from the migration script. Destination writes are transactional. Run `--dry-run` before the real migration.
