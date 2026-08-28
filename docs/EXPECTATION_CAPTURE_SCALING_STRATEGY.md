# Expectation Capture Scaling Strategy

## What the first live capture proves
It proves adapter execution, append-only storage, source lineage and strict-PIT chronology. It does not prove forecast value.

## Why 287 rows per symbol needs inspection
A provider endpoint can expose current estimates, historical horizons and embedded revision fields. Equal normalized row counts may be a schema property, but any repeated series identity within one source snapshot must be understood before a transformer labels it a time-series revision.

## Cadence
- Deep 10-company cohort: daily.
- Known earnings within 7 calendar days: daily.
- Earnings within 30 days: every 2 days.
- Other broad-universe names: weekly.

This selection is causal because event proximity is known at capture time. It is an acquisition policy, not a trading rule.

## Future feature research (blocked now)
Possible latent observables include current consensus level, estimate width, analyst count, revision direction/magnitude, disagreement, and time-to-event. They must be created only in a separately preregistered experiment.
