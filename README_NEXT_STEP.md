# Next step — Event Brain Foundation v0.2

The Event Layer is the current development track. Market Brain V002 remains
frozen as the baseline and Macro has not started yet.

## Current database state

Migrations already applied:

- 009: historical observable universe;
- 010: causal Event Layer tables;
- 011: immutable source-document and SEC metadata foundation.

Do **not** apply `database/migrations/009_event_layer.sql`. It is an obsolete
package artifact that conflicts with the applied migration numbering.

The canonical Event Layer contract is:

- `events`: event identity and metadata;
- `event_assets`: canonical asset links;
- `event_clusters` / `event_cluster_news`: document clustering;
- `event_evidence`: evidence available over time;
- `event_states`: causal temporal snapshots;
- `event_reaction_outcomes`: later observed reactions;
- `event_source_knowledge`: learned source/context behavior.

## Validate the foundation

Run:

```bash
python -m pytest -q tests/test_event_layer_contract.py
python -m evaluation.diagnostics.validate_event_causality
python -m evaluation.diagnostics.audit_event_lineage \
  --output data/processed/event_lineage_audit.json
```

With the current empty Event Layer, causality should report `INCOMPLETE`,
not `PASS`. The lineage audit should distinguish referential integrity from
training readiness.

## Current legacy limitations

The inherited corpus is evidence for reconstruction, not a training-ready
event dataset:

- most documents are SEC 8-K summaries;
- URLs, full raw documents and source payloads were not preserved;
- most legacy events contain one article;
- event time, clusters, evidence states and reaction labels are absent;
- historical news coverage is much longer than the available minute prices.

Do not train an Event Brain from these rows yet.

## Next implementation

Completed and verified:

- source registry, ingestion runs and immutable raw-document contract;
- SEC v2 recent-submissions pilot;
- exact official SEC response bytes plus SHA-256;
- normalized filing metadata linked to the raw parent and AAPL;
- causal `available_at` from SEC `acceptanceDateTime`;
- idempotent reruns without duplicate filings.

The next bounded block is:

```text
SEC filing index + primary documents/exhibits
        -> historical daily prices
        -> deterministic document clustering
        -> event normalization and asset linking
        -> causal event states
        -> reaction targets
        -> source/context learning
        -> incremental Event Brain benchmark
```

The current pilot does not yet download primary filing documents or exhibits;
`sec_filing_files` is intentionally empty. No events or trading signals are
created directly by ingestion.

Macro follows after this event-data contract and the corresponding historical
market coverage are stable. Macro data must use release-time/vintage semantics
so revised future values cannot leak into historical features.

