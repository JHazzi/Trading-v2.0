# Information Source Capture V001

Non-predictive overlay that extends the strict-PIT expectation foundation with:

- a source/provider audit registry;
- a model-invisible Alpha Vantage earnings expectation adapter;
- a global earnings-calendar raw snapshot capture;
- a ten-symbol analyst-estimate pilot capture;
- explicit refusal to fabricate exact event timestamps from date/daypart calendar data;
- V009 isolation audit;
- canonical-doc additive patcher.

No V009 file, model artifact, training hash, feature family, target or gate is changed. `alpha_vantage.enabled` defaults to `false`; live capture requires an explicit config edit plus `ALPHAVANTAGE_API_KEY`.

This package deliberately does not make historical Alpha Vantage estimates strict PIT. Strict PIT applies to observations actually retrieved prospectively by this system.
