# Market Features

This package builds reproducible market-state features.

Current implemented research includes intraday Market State V002 and daily market context used by the Event Brain.

The daily Event Brain context currently contains relatively simple asset/cross-sectional/sector features and is **not yet the intended Market Brain Daily V003**.

Next market-state work should add stronger strictly-as-of context such as broad-market, style, sector, rates, volatility and regime features, then demonstrate OOS value against trivial baselines.

Do not add future event/news outcome information to Market State.

Canonical references:

- `../../ARCHITECTURE.md`
- `../../docs/RESEARCH_STATUS.md`
- `../../docs/ROADMAP.md`
- `../../docs/DATA_CONTRACTS.md`
