# Event Ingestion

This package owns source/evidence ingestion and normalization boundaries for the Event Layer.

Current SEC research flow:

```text
SEC metadata
→ raw filing documents/exhibits
→ deterministic clustering
→ stable event normalization
→ downstream Event State
```

Important modules include:

- `sec_edgar_v4_history.py` — deep historical metadata retrieval;
- `sec_filing_documents_v2.py` — causal raw document/exhibit retrieval;
- `deterministic_clustering.py` — evidence clustering;
- `sec_event_normalizer_v003_deep.py` — current deep SEC normalization adapter;
- `sec_metadata_logic.py` — metadata selection/version logic.

Ingestion must not assign hardcoded predictive impact, reliability or direction.

Current deep experiment uses SEC forms:

```text
8-K 8-K/A 10-Q 10-Q/A 10-K 10-K/A
```

Historical deep evidence is research reconstruction and remains PIT=0.

Canonical references:

- `../../ARCHITECTURE_EVENT_LAYER.md`
- `../../docs/DATA_CONTRACTS.md`
- `../../docs/RESEARCH_STATUS.md`
