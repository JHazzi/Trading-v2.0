# Scale Event Brain data v0.1

The end-to-end Event Brain pipeline is working. The current bottleneck is data
diversity, not model complexity.

This block uses a manifest-driven research cohort of 10 companies: two peers in
each of five broad sectors. The cohort is not a model prior and is not tied to
S&P 500 membership. A future company outside the index can be added after it has
an asset identity and temporal universe membership.

## Important SEC fix

Migration 016 existed, but the public SEC v2 writer did not natively append
`sec_submission_retrievals`, `sec_filing_metadata_versions` and
`sec_filing_metadata_observations` on new retrievals. `sec_edgar_v3.py` closes
that gap and preserves initial / unchanged / revision / reversion observations.

Historical first observations use `acceptance_datetime` as a research
availability proxy and mark PIT=0. Later retrieval-time corrections are PIT=1.
A future live collector should use `--initial-availability-mode live`.

## Intelligence logic to preserve

A positive earnings report is not assumed bullish. Later Event Brain features
must keep separate:
- actual outcome;
- expectation / consensus / prior guidance;
- surprise versus expectation;
- pre-event run-up or drawdown;
- market and sector state;
- post-event first reaction, only for prediction timestamps after that reaction;
- factual reliability, novelty, predictive utility, impact and persistence.

"Reliability" must not collapse all of these into one fixed source score.
