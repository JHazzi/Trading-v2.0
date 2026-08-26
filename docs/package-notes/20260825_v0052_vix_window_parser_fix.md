# V005.2 VIX window parser fix

The Cboe 1990-to-present VIX CSV contains a legacy row dated 1992-02-11 that
fails the strict OHLC invariant used by V005.2.

The research contract does not use that period. V005.2 requires:

```text
2016-01-01 <= trading_day < 2026-08-25
```

The original parser validated every row in the full provider file before
applying the configured research window. Therefore an irrelevant legacy row
could block a valid 2016+ acquisition.

This fix changes only the order:

```text
parse date
-> discard out-of-window row
-> parse/validate OHLC for in-window rows
```

No quality gate is weakened inside the configured research window. An invalid
OHLC row in 2016-2026 still aborts acquisition.

The raw Cboe file remains stored content-addressed in full; only the normalized
research observations are window-scoped.
