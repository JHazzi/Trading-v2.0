# Investment Workbench V0

Evidence-aware, read-only product surface for Quant Market AI.

V0.1 adds:

- tabbed product navigation;
- historical → `Ahora` → future scenario chart;
- H1 / H5 / H10 horizon selector;
- q50 central, q75 bullish, q25 bearish scenario paths;
- q05–q95 uncertainty fan;
- non-monotonic published confidence curve;
- scheduled event markers;
- in-product explanations of horizons and quantiles;
- explicit demo-mode warning for synthetic sample paths.

Run:

```bash
python -m product.workbench_v0.server \
  --state product/workbench_v0/sample_state.json \
  --journal reports/workbench/decision_journal.jsonl
```

Open `http://127.0.0.1:8765`.

The included sample state is deliberately synthetic and is not a live AAPL prediction. A real Research → InvestmentState publisher must provide observed history, pathwise forecast quantiles and confidence scores before those surfaces can be interpreted as model output.
