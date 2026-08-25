# Deep Event V003.1 — SEC raw-lineage and temporal semantics

## Incident detected

After 10 successful deep clustering runs, V003 normalization produced:
- AAPL: 5 filings considered, 2 events;
- all other tickers: 0 filings/events.

This was not an economic result.

The normalizer selected SEC evidence by requiring:

    cluster membership
      -> event_cluster_sec_observation_refs
      -> sec_filing_file_observations
      -> filing

That relation is correct for strict temporal retrieval evidence but incomplete
for historical research backfills downloaded in 2026.

## Why the old gate was wrong

For a historical filing:
- the SEC acceptance/publication timestamp can be known historically;
- the archived filing bytes can be downloaded today;
- the actual retrieval observation is therefore today;
- the reconstructed historical availability remains PIT=0.

Requiring `observed_at <= historical cutoff` deletes reconstructed history.
Changing the retrieval timestamp to the old acceptance time would be worse:
it would fabricate strict point-in-time provenance.

## V003.1 contract

Economic/research lineage:

    clustering membership
      -> raw membership ref
      -> immutable raw document id
      -> SEC filing file version
      -> filing/accession

Temporal confidence:

- `membership.availability_is_point_in_time = 0`
  - historical acceptance/evidence time may be used as an explicit research
    availability proxy;
  - actual retrieval timestamp is retained as provenance;
  - no strict PIT claim is allowed.

- `membership.availability_is_point_in_time = 1`
  - a temporal SEC observation ref must exist by the cutoff;
  - otherwise the normalizer fails instead of silently downgrading/upgrading.

This separates:
1. what economic filing/document the evidence belongs to;
2. when the information is treated as available in research;
3. when bytes were actually retrieved;
4. whether strict PIT is justified.

## Why this is aligned with ARCHITECTURE.md

The architecture requires:
- separation of raw observations and derived inference;
- timestamps and versions for features;
- only information available at t;
- corrected data to preserve actual availability where possible;
- reproducible walk-forward evaluation.

The fix does not invent an old retrieval timestamp and does not convert
historical backfills into PIT observations.

## Audit hardening

A single `PASS` was too weak.

V003.1 reports independent stage statuses:
- clustering;
- normalization;
- states;
- labels.

If normalization has been run but nine of ten runs produce zero output, overall
status becomes REVIEW. A successful upstream stage can no longer hide a broken
downstream stage.

## Remaining scientific caveats before a strong predictive claim

Even after data repair:
- this is a current-company research cohort, not a survivorship-free market;
- historical SEC backfill is non-strict-PIT;
- multiple events from the same accession are correlated training examples;
- adjacent 10-session outcomes overlap;
- the daily Market Brain baseline remains weaker than zero in the first pilot;
- sector context is thin with roughly two companies per sector;
- consensus/expectations are not yet represented.

Therefore the next deep benchmark is evidence about signal robustness, not a
production-alpha claim.
