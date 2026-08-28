# Information Capture Orchestrator V0013

Purpose: operational hardening before wider expectation capture.

## Why now

The first live captures contain 19 symbols and 5,411 normalized expectations.
Canonical quarter/year series collisions are zero. DELL has 245 rows, which is
35 complete seven-observable provider periods rather than the common 41; this is
treated as a coverage difference, never padded.

## Additions

1. `provider_request_observations`: append-only request ledger. Attempts count even
   when a provider call fails.
2. rolling-24h conservative quota planning. Initial successful Alpha Vantage source
   observations are backfilled into the ledger so the current window is not reset.
3. `scheduled_event_window_observations`: date-only scheduled earnings are normalized
   without inventing an exact timestamp.
4. coverage audit: verifies completeness inside each provider period while allowing
   symbols to expose different numbers of periods.

No model sees these records. V009 remains frozen and isolated.
