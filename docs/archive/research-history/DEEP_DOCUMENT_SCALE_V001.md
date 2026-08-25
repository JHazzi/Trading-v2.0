# Deep historical SEC documents v0.1 — scale contract

## Why this stage exists

Metadata scale is now complete for the 10-company cohort. The next expensive
operation is raw SEC document retrieval.

The project must not equate "metadata exists" with "this example can train the
Event Brain". A filing is worth processing for this experiment only if the
target asset has enough daily history to build the existing 20-session Market
State and if a daily price still exists at/after the event.

## Eligibility

For each asset:

1. get distinct quality-gated daily trading days;
2. define `ready_day` as the 21st available trading day;
3. define `last_day` as the latest quality-gated trading day;
4. select the latest metadata version/observation of configured SEC forms;
5. keep the filing only when:

       ready_day <= acceptance_day <= last_day

This is not an economic assumption. It is a data-availability requirement for
the current feature/label contract.

## Reuse before retrieval

A filing that already has at least one persisted downloaded raw document is not
selected as pending. This prevents the prior 2024-2026 pilot corpus from being
requested from SEC again.

Raw storage/content addressing and migration-016 metadata selections remain
handled by `sec_filing_documents_v2`.

## Why oldest-first

Pending filings are processed globally from oldest acceptance time to newest.

If a long run is interrupted, the partial corpus gains historical regime depth
instead of merely increasing density in the recent period that is already well
represented.

## What this stage does NOT do

It does not:
- cluster;
- normalize economic events;
- create Event States;
- create reaction labels;
- train any model;
- change the 10-session hypothesis;
- treat historical backfill as strict point-in-time capture.

After document completion, a separate scale package will rebuild a versioned
deep event-state corpus and explicitly protect the existing pilot lineage.
