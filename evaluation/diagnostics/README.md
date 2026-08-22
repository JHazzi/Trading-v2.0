# Diagnostics — Market Foundation v0.1

The first evaluation layer validates the target engine before any model is trusted.

Minimum invariants:

- `MAE_T <= R_T <= MFE_T`
- `0 <= coverage_pct <= 100`
- `observed_bars <= expected_bars`
- positive start/end prices

Run:

```bash
python evaluation/diagnostics/validate_outcomes.py
```
