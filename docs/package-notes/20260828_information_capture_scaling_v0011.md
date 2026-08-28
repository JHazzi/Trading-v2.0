# Information Capture Scaling V0011

Purpose: scale strict-PIT analyst-expectation capture without turning capture volume into a predictive claim and without interacting with V009.

Key decisions:
- Quality-audit the first live snapshot before broad scaling.
- Same-snapshot duplicate series are diagnostics, never silently interpreted as temporal revisions.
- Capture cadence follows information economics: deep cohort daily; earnings <=7d daily; <=30d every 2d; broad universe weekly.
- Default provider request budget is 20/day, leaving reserve under Alpha Vantage's documented standard 25-request/day limit. Users with verified open-source/education or premium entitlements may explicitly raise the budget.
- Checkpoint/resume is mandatory for batch capture.
- Repeated unchanged snapshots are retained: "no revision" is itself an observed state. Feature visibility remains blocked.
- No V009 file/config/model imports this package.

Storage warning: 287 normalized observations x 497 symbols is 142,639 rows per full snapshot; daily full-universe capture would exceed 52M rows/year. Broad-universe daily capture is therefore not the default.
