# Information Archaeology V001

Read-only audit of the project's existing information foundations before further scaling.

Primary target:
`data/database/market_data_v2.db`

The tool inventories:
- raw source documents;
- news documents/assets/features;
- event-news and event-cluster layers;
- evidence semantics and source knowledge;
- event states and reaction outcomes;
- normalized event identities/observations/labels;
- relation evidence and temporal relation layers;
- market sessions and price bars;
- prediction lifecycle tables.

It also discovers processed relation / identity / graph SQLite databases and records their
table counts, sizes, time fields, key null fractions, explicit PIT fields, source concentration,
foreign-key edges and orphan counts.

The audit does not mutate databases, call providers, create features, train models or change V009.

Purpose:
1. determine what already exists;
2. avoid duplicating News/Event/Graph architecture;
3. identify which existing layers satisfy the current causal contract;
4. choose the next scaling action from evidence rather than assumptions.
