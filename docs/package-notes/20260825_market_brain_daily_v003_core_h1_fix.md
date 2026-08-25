# Market Daily V003 Core — H1 Label Fix

The first Core audit incorrectly passed despite:

```text
H1 rows                 1,092,555
H1 usable                       0
H1 insufficient_future  1,092,555
```

Root cause: `Rolling.std()` defaults to sample standard deviation
(`ddof=1`). H1 contains one future daily return, so the result was `NaN`.

Correction:

```text
realized_path_vol_pct uses ddof=0
H1 path volatility = 0
```

The audit now fails if an expected horizon is absent, if any horizon has
less than 50% usable labels, or if usable H1 path volatility is not finite
and exactly zero.

Rebuild the processed Core DB; do not patch rows in place.
