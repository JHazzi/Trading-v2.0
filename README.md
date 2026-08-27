# Quant Market AI

Research system for probabilistic market forecasting.

The long-term goal is not to predict a single future price. The project aims to learn a **conditional distribution of future market trajectories** from information that was legitimately available at prediction time:

\[
P(R_{t:t+T}\mid X_t,E_t,G_t,T)
\]

where `X_t` is the causal market state, `E_t` is the event/information state, `G_t` is the relationship/graph state and `T` is the requested horizon.

The observed market price is the training/evaluation ground truth. News, SEC filings, macro data and graphs are information sources; they do not replace the Market Brain and they are not hardcoded into fixed economic-impact rules.

> **Research status:** experimental. This repository is not a production trading system and current results must not be interpreted as validated alpha.

## Current checkpoint — 2026-08-26

The project has now closed the first robustness and daily-market checkpoint rather than only building infrastructure.

The deep SEC corpus remains fixed at 1,939 normalized events and 2,001 causal Event States for the current 10-asset research cohort. Event Brain H10 survived only conditionally: 4/5 Random Forest seeds produced a positive capacity-matched delta (mean `+0.0189 pp`, median `+0.0284 pp`), while simple linear families were negative and the early-OOS sensitivity was approximately null. This is nonlinear, unstable candidate information—not confirmed event alpha.

The scalar Daily Market Brain sequence is also closed as evidence:

- V003 lost to the fold train median at H1/H3/H5/H10;
- V004 factorization materially improved V003 but still lost to the median at every horizon;
- V005.1 SPY/QQQ/IWM and V005.2 financial conditions did not pass their incremental gates over V004;
- no scalar daily model is promoted.

The first Distributional Market Brain foundation, V006, tests a simpler question: whether a train-only empirical return distribution becomes better calibrated when its width is rescaled by the causal 20-session asset volatility known at prediction time.

| Horizon | OOS rows | Origin-day pinball delta: baseline − scaled | 95% moving-block CI (10 days) |
|---:|---:|---:|---:|
| H1 | 763,935 | +0.00920 pp | [+0.00654, +0.01150] |
| H3 | 743,503 | +0.01349 pp | [+0.00833, +0.01827] |
| H5 | 723,573 | +0.01342 pp | [+0.00709, +0.01996] |
| H10 | 673,391 | +0.01266 pp | [+0.00257, +0.02481] |

All four preregistered horizons improve under 5/10/20-origin-day block bootstraps. Central 50% coverage is approximately 50%; central 90% coverage is 90.5–91.0%. However, median MAE is essentially unchanged and positive-return Brier score is slightly worse. The supported claim is therefore **better conditional dispersion/interval scale**, not better direction, expected return, trajectory prediction or tradable alpha.

The Event–Graph identity foundation produced 28 conflict groups, 30 review pairs and 3 row-quality candidates. Its audit is intentionally `REVIEW`: no canonical entities, exclusions, graph edges or main-database mutations were created.

> **Current claim:** the project now has a reproducible positive baseline for conditional return uncertainty. It still has no validated directional alpha, event alpha, path generator or production trading policy.

## What happens next

1. **Distributional Market Brain V006.1 robustness/falsification** — temporal, asset, tail, regime and alternative-scale diagnostics without changing the completed primary claim.
2. **Learned Distributional Market Brain** — only under a new preregistration with nested temporal model selection and the empirical V006 baseline as a required control.
3. **Distributional Event Brain** — test whether the existing SEC Event State adds calibrated information beyond a capacity-matched market-only distribution.
4. **Identity hygiene before graph promotion** — resolve the 30 reviewed pairs and 3 row-quality candidates upstream; graph propagation remains blocked.
5. Richer event semantics/expectations, then additional sources, only when incremental OOS evidence justifies them.
6. Coherent path distributions, risk and decision layers after terminal distributions are credible.
7. Controlled candidate/promotion/rollback learning; never blind online self-mutation.

No additional SEC scaling, production alerts or live trading is justified at this checkpoint.

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
