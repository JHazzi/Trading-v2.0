# V005.2 — VIX provider rows on non-equity dates

During Cboe VIX acquisition, the provider file contained an in-window row dated
2022-05-30, while XNYS was closed for Memorial Day.

Policy:

- preserve the provider row in raw/version/observation lineage;
- do not invent an equity-session close timestamp for it;
- assign retrieval-time availability and an explicit non-model-eligible basis;
- exclude non-XNYS VIX dates before any rolling or lag operation;
- therefore `vix_lag1_*` means previous XNYS session, not previous arbitrary
  provider date;
- the foundation audit fails if a provider-only VIX date leaks into the model
  state.

The existing ETF total-return proxy using effective cash distributions is
unchanged by this fix.
