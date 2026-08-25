# Quant Market AI

Research system for probabilistic market forecasting.

The long-term goal is not to predict a single future price. The project aims to learn a **conditional distribution of future market trajectories** from information that was legitimately available at prediction time:

\[
P(R_{t:t+T}\mid X_t,E_t,G_t,T)
\]

where `X_t` is the causal market state, `E_t` is the event/information state, `G_t` is the relationship/graph state and `T` is the requested horizon.

The observed market price is the training/evaluation ground truth. News, SEC filings, macro data and graphs are information sources; they do not replace the Market Brain and they are not hardcoded into fixed economic-impact rules.

> **Research status:** experimental. This repository is not a production trading system and current results must not be interpreted as validated alpha.

## Current checkpoint — 2026-08-25

The project has completed a deep SEC research corpus and a first scalar Event Brain replication:

- 10-asset research cohort;
- common scientific window: `2016-09-23` through `2026-08-24`;
- 1,704 eligible SEC filings;
- 10,642 persisted cohort evidence records;
- 1,939 normalized economic event identities;
- 2,001 causal Event States;
- 8,004 reaction labels across 1/3/5/10 sessions;
- 6,343 usable labels after conservative exclusions.

Model-ready rows:

| Horizon | Rows | Unique events |
|---:|---:|---:|
| 1 session | 1,700 | 1,650 |
| 3 sessions | 1,667 | 1,620 |
| 5 sessions | 1,619 | 1,573 |
| 10 sessions | 1,353 | 1,314 |

The current scalar Event Brain experiment asks whether event features add information beyond a capacity-matched residual control:

| Horizon | MAE delta: control − contextual |
|---:|---:|
| 1 | +0.0043 pp |
| 3 | −0.0038 pp |
| 5 | −0.0136 pp |
| 10 | **+0.0282 pp** |

At 10 sessions the effect is positive in all four OOS folds, but its paired bootstrap 95% interval still crosses zero. Treat H10 as a **weak candidate signal**, not a confirmed effect.

The bigger unresolved issue is the daily Market Brain: it does not consistently beat trivial zero/median baselines OOS. Improving the base market distribution is therefore a higher priority than adding more event sources.

## What happens next

1. **Event Brain V0.2.1 robustness** — try to falsify H10.
2. **Market Brain Daily V003** — build a stronger strictly-as-of market context.
3. **Distributional Market Brain** — quantiles, probabilities and calibration.
4. **Distributional Event Brain** — test whether events improve median, width, tails, path volatility and MFE/MAE.
5. Richer event semantics — filing text, numeric facts, guidance, expectations and surprise.
6. Additional sources — IR, news/wires, macro and analyst expectations.
7. Structural/statistical/learned graphs.
8. Trajectory engine, risk, decision layer and paper trading.
9. Controlled continuous-learning / candidate-promotion loop.

No additional SEC scaling is planned before robustness and Market Brain work.

## Documentation map

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — long-term system contract.
- [`ARCHITECTURE_EVENT_LAYER.md`](ARCHITECTURE_EVENT_LAYER.md) — event/news semantics and causality.
- [`AGENTS.md`](AGENTS.md) — rules for humans/AI agents modifying the repository.
- [`docs/INDEX.md`](docs/INDEX.md) — documentation source-of-truth map.
- [`docs/RESEARCH_STATUS.md`](docs/RESEARCH_STATUS.md) — current empirical checkpoint.
- [`docs/RESEARCH_DECISIONS.md`](docs/RESEARCH_DECISIONS.md) — durable decisions and hypothesis history.
- [`docs/ROADMAP.md`](docs/ROADMAP.md) — ordered next work with gates.
- [`docs/EXPERIMENTS.md`](docs/EXPERIMENTS.md) — experiment registry.
- [`docs/DATA_CONTRACTS.md`](docs/DATA_CONTRACTS.md) — temporal/PIT/lineage contracts.
- [`docs/DOCUMENTATION_POLICY.md`](docs/DOCUMENTATION_POLICY.md) — where future ZIP/package MDs belong.

Historical package notes are preserved under `docs/archive/`; they are not current instructions.

## Repository principles

1. Market Brain must work without events/news.
2. Source documents are evidence, not shocks.
3. `available_at` gates feature usability.
4. Historical reconstruction must never be mislabeled strict PIT.
5. Do not hardcode source reliability, event impact, direction, persistence or graph strength when these can be learned.
6. Never use random train/test split as the primary time-series evaluation.
7. Models, features, labels, data selection and predictions are versioned.
8. Prediction, risk and trading decisions remain separate layers.
9. New complexity must earn its place through out-of-sample incremental evidence.
10. Old experiments are preserved for reproducibility, but historical READMEs are not canonical documentation.

## Quick research checks

```bash
python -m pytest -q
```

Deep Event Corpus audit:

```bash
python -m pipeline.event_brain_deep_corpus_v003 --stage audit
```

Deep Event Brain dataset audit:

```bash
python -m pipeline.event_brain_deep_benchmark_v0031 \
  --stage audit \
  --horizons 1,3,5,10
```

See `docs/RESEARCH_STATUS.md` before interpreting any result.
