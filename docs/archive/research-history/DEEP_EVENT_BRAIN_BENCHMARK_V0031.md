# Deep Event Brain benchmark V003.1

This is a dataset-depth replication, not a new model.

The runner executes the frozen Event Brain V0.2 training/evaluation code but
explicitly binds it to:

- `event_state_v0031_deep`
- `event_reaction_daily_v0031_deep`
- `daily_asset_cross_section_sector_v002_leave_one_out`

The old V0.2 source files remain unchanged. This matters because the original
trainer and `audit_horizon()` default to the pilot `event_state_v002` /
`event_reaction_daily_v002` dataset. Running them directly would silently test
the old ~300-event corpus again.

Primary scientific question: does the tentative 10-session event contribution
survive a ~10-year, ~1.3k model-ready-event H10 dataset while the model and
walk-forward contract stay fixed?

Remaining caveats after a positive result:

- historical SEC evidence is reconstructed and PIT=0;
- current-company cohort has survivorship bias;
- same-event states are correlated, though outer folds are event-grouped;
- H10 targets overlap in calendar time;
- origin-day bootstrap is not yet horizon-block bootstrap;
- daily Market Brain was weak against zero in the pilot;
- corporate-action exclusions are substantial at H10;
- no consensus/expectation surprise features exist yet.

Therefore a positive H10 result should trigger robustness work before any new
source family or model complexity.
