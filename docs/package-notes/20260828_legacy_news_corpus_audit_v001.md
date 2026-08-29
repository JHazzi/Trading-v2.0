# Legacy News Corpus Audit V001

This package performs no network calls and no modeling.

It inspects the existing SQLite legacy news corpus before any attempt to correlate
documents with market reaction.

Outputs include:
- news/price schema discovery;
- timestamp parse and timezone-awareness diagnostics;
- temporal coverage;
- source and ticker concentration;
- normalized-title and URL duplicate diagnostics;
- cross-source/cross-ticker repetition;
- sentiment-field inventory;
- price-granularity diagnostics;
- a hard gate indicating whether reaction alignment is currently scientifically safe.

The audit deliberately does NOT calculate a news-return correlation unless time semantics
are established. A misleading correlation is worse than no statistic.

Default database:
`data/database/market_data.db`

Default output:
`reports/news/legacy_corpus_audit_v001.json`
