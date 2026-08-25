# Intelligence push: from causal infrastructure to Event Brain

## Non-negotiable architecture

The objective remains:

`P(R[t:t+T] | X_t, E_t, G_t, T)`

- Market Brain must predict without news.
- Events modify the base distribution; they do not trigger prediction.
- Source reliability, economic importance, surprise, persistence and decay are learned.
- Every prediction is persisted and later linked to its realized outcome.
- Continuous learning uses rolling evaluation, drift detection, candidate training,
  walk-forward comparison, promotion and rollback. Never blind online overwrite.

## Multi-source event policy

SEC is only the first clean primary source.

All future sources must enter through the same raw/observation contract and keep source identity:
- SEC EDGAR.
- Company Investor Relations / official press releases.
- Reputable news wires / newspapers where access and licensing permit it.
- Exchange notices and trading halts.
- Later: Fed/BLS/ALFRED and other macro release/vintage sources.

Do not assign fixed source scores. Source/context reliability is a learned output.

## Immediate cutoff for infrastructure

Only close these before event semantics:

1. Integrate migration 016 into fresh bootstrap.
2. Apply/test 016 on the real DB.
3. Use `daily_price_projection.py` as the only approved read path for new daily
   reaction targets:
   - `target_final`: final quality-approved truth for labels.
   - `research_asof`: causal research reconstruction, explicitly not verified PIT.
   - `strict_pit`: exact-replay claims only.

After these pass, stop adding ingestion infrastructure and move to:

4. Event normalization (versioned, factual, no impact/sentiment).
5. Event state snapshots.
6. Daily reaction targets (1/3/5/10 sessions).
7. First Event Brain benchmark:
   - Market only
   - Event only
   - Market + Event
   on identical walk-forward samples.
8. Persist predictions/outcomes so the continuous-learning loop can begin collecting evidence.

## Continuous learning later

The first working loop should be system-level, not blind per-tick model mutation:

prediction
→ realized outcome
→ diagnostics/calibration/drift
→ candidate training
→ walk-forward benchmark
→ champion/candidate comparison
→ promote or reject
→ rollback if production degrades

The architecture should start collecting the necessary prediction/outcome records as soon
as Event Brain v0.1 exists, even before automatic retraining is enabled.
