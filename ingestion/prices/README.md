# Price Ingestion

This package owns price-source ingestion and market-session projection boundaries.

Current research sources/components include:

- historical/intraday price data migrated from the legacy corpus;
- `sessionizer.py`;
- `yahoo_daily_v1.py` for initial daily historical research observations.

Yahoo Finance is an initial research source, not assumed perfect ground truth.

Historical daily availability may use an explicit research proxy when strict provider PIT history is unavailable. The data contract must state this clearly.

Do not let adjusted/revised future knowledge leak into historical prediction features.

Canonical references:

- `../../ARCHITECTURE.md`
- `../../docs/DATA_CONTRACTS.md`
