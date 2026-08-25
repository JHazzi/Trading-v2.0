# Event Models

The Event Brain has been trained experimentally; this directory is no longer “not trained yet”.

Current model families:

- `train_v001.py` — first bounded event experiment;
- `train_v002.py` — capacity-controlled scalar Event Brain experiment;
- `train_v0031_deep.py` — frozen V0.2 training logic explicitly evaluated on deep V003.1 state/label versions.

Current deep scalar question:

> Does factual SEC Event State add incremental information beyond a capacity-matched market-only residual control?

Result summary:

- H1: approximately zero;
- H3: approximately zero/slightly negative;
- H5: slightly negative;
- H10: weak positive candidate, not confirmed.

This scalar `return_pct` experiment is not the target architecture.

Future Event Brain should compare distributional forecasts:

```text
F0(Y | market_state, horizon)
vs
F1(Y | market_state, event_state, graph_state, horizon)
```

Candidate incremental outputs:

- median-return shift;
- quantile/tail shift;
- uncertainty/width;
- path volatility;
- MFE/MAE;
- regime probability.

Do not interpret RF tree dispersion as calibrated market uncertainty.

Canonical references:

- `../../ARCHITECTURE_EVENT_LAYER.md`
- `../../docs/RESEARCH_STATUS.md`
- `../../docs/EXPERIMENTS.md`
- `../../docs/ROADMAP.md`
