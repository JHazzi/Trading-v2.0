# Market Brain Daily V005 — External Market State Foundation

V004 factorization is retained: it materially repaired V003 but still lost the
absolute-return primary to train median. V005 therefore tests the next
Architecture Phase C hypothesis: the base Market Brain needs more information
available at prediction time.

Stage 1 is intentionally limited to:

```text
SPY  broad large-cap market state
QQQ  growth/technology tilt
IWM  small-cap/risk tilt
```

No news, macro releases, VIX, sector ETFs or rates/credit enter yet.

## Commands

```bash
python -m pytest tests/test_market_brain_daily_v005_external_state_foundation.py -q

python tools/patch_market_v004_results_v005_docs.py --check
python tools/patch_market_v004_results_v005_docs.py --apply

python -m pipeline.market_brain_daily_v005_external_state --stage acquire
python -m pipeline.market_brain_daily_v005_external_state --stage build
python -m pipeline.market_brain_daily_v005_external_state --stage audit
```

Send:

```text
reports/market_brain_daily_v005/external_state_foundation_audit.json
```

Do not train V005 before that audit.

## Causal limitation

Historical Yahoo ETF data is reconstructed now and therefore is NOT strict
point-in-time provider replay. For this research stage it uses the same
historical-session-close assumption as prior daily research. It must not be
described as production PIT data.

The next benchmark must add this block only to the market-level component of
V004, keep sector/asset models unchanged, reuse V004 folds and compare paired
OOS rows.
