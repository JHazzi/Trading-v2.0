# Next execution — Temporal Distributional V002

Run from `/home/trabajo/quant_market_ai` with the project virtual environment.
The commands are resumable and write only under
`reports/temporal_distributional_runner_v002/`. Source V002, Core and the
selection mask are opened read-only. V009 is not loaded.

## 1. Verify code and rebuild the two plan gates

```bash
source .venv/bin/activate

python -m py_compile \
  tools/temporal_distributional_preregistration_v002.py \
  models/market/temporal_distributional_v002.py \
  evaluation/market/temporal_distributional_v002.py \
  pipeline/market_temporal_distributional_v002.py

python -m unittest \
  tests.test_market_temporal_distributional_v001 \
  tests.test_market_temporal_distributional_v002 -v

python tools/temporal_distributional_preregistration_v002.py --stage plan
python pipeline/market_temporal_distributional_v002.py --stage plan
```

Do not continue unless the second command reports `status: PASS`, zero
failures, `development_training_authorized: true`,
`holdout_outcomes_or_performance_read: false` and
`v009_loaded_or_modified: false`.

## 2. Execute development folds sequentially

```bash
for fold in 1 2 3 4 5; do
  python pipeline/market_temporal_distributional_v002.py \
    --stage develop-fold \
    --fold "$fold"
done

python pipeline/market_temporal_distributional_v002.py \
  --stage develop-aggregate

python pipeline/market_temporal_distributional_v002.py \
  --stage status
```

Sequential execution is recommended because each shard fits causal inner-base,
residual and five placebo families. A completed fold is reused only when its
report and artifact hashes match, so the loop can be rerun after interruption.

The terminal evidence is:

```text
reports/temporal_distributional_runner_v002/development_summary.json
```

## 3. Obey the development result

If status is `FAIL_CLOSE_TEMPORAL_DISTRIBUTIONAL_V002_BRANCH` or
`INCONCLUSIVE_OR_AUXILIARY_GATE_FAIL_NO_HOLDOUT_OPEN`, do not run any holdout
command. Close the experiment:

```bash
python pipeline/market_temporal_distributional_v002.py --stage close-branch
python pipeline/market_temporal_distributional_v002.py --stage status
```

If and only if status is `PASS_DEVELOPMENTAL_REQUIRES_FRESH_HOLDOUT`, first
freeze every development artifact:

```bash
python pipeline/market_temporal_distributional_v002.py --stage freeze
python pipeline/market_temporal_distributional_v002.py --stage status
```

Stop there and review `development_summary.json` plus
`development_freeze.json` before the irreversible one-time holdout opening.
The holdout commands are deliberately not included in this execution block.

## Interpretation

A development pass is not promotion. It authorizes one fresh interpolation
holdout evaluation with unchanged models. A failure closes this residual idea;
it does not authorize changing the half-life, margin, features or anchors. An
inconclusive result also leaves holdouts sealed. No outcome here changes V009.
