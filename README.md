# Quant Market AI

Research system for probabilistic market forecasting.

The long-term goal is not to predict a single future price. The project aims to learn a **conditional distribution of future market trajectories** from information that was legitimately available at prediction time:

\[
P(R_{t:t+T}\mid X_t,E_t,G_t,T)
\]

where `X_t` is the causal market state, `E_t` is the event/information state, `G_t` is the relationship/graph state and `T` is the requested horizon.

The observed market price is the training/evaluation ground truth. News, SEC filings, macro data and graphs are information sources; they do not replace the Market Brain and they are not hardcoded into fixed economic-impact rules.

> **Research status:** experimental. This repository is not a production trading system and current results must not be interpreted as validated alpha.

## Recover context before changing anything

Read [AGENTS.md](AGENTS.md) and the canonical documents in its required order.
The empirical checkpoint lives only in [docs/RESEARCH_STATUS.md](docs/RESEARCH_STATUS.md);
current priorities live in [docs/ROADMAP.md](docs/ROADMAP.md). Historical notes
are not instructions to rerun completed experiments.

For actual local databases, schemas, sources, counts, versions, experiment
evidence and Git/local differences, generate the read-only context report:

    python3 tools/project_context.py

Then open reports/project_context/latest/CONTEXT.md. Before reusing it:

    python3 tools/project_context.py --check

See [docs/CONTEXT_RECOVERY.md](docs/CONTEXT_RECOVERY.md). It works with Python's
standard library, does not train or mutate research data, and explicitly marks
missing/unknown evidence. If an AI only has GitHub access, ask the user to run
it on the machine containing the databases.

The report is local and ignored by Git; it is metadata, not a database backup.
A failed experiment remains protected scientific evidence, not a cleanup target.

## Documentation map

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — long-term system contract.
- [`ARCHITECTURE_EVENT_LAYER.md`](ARCHITECTURE_EVENT_LAYER.md) — event/news semantics and causality.
- [`AGENTS.md`](AGENTS.md) — rules for humans/AI agents modifying the repository.
- [`docs/CONTEXT_RECOVERY.md`](docs/CONTEXT_RECOVERY.md) — evidence-based context recovery and safe cleanup.
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
