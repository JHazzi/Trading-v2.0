# Next Execution — Temporal V002 Review and Model Preregistration V001

Run from `/home/trabajo/quant_market_ai` in the existing environment. These
commands are read-only with respect to V002, Core, V001, source and V009.

## 1. Verify code

```bash
python -m py_compile \
  tools/temporal_v002_review.py \
  tools/temporal_distributional_preregistration_v001.py

python -m unittest \
  tests.test_temporal_dataset_v001 \
  tests.test_temporal_dataset_v002 \
  tests.test_temporal_v002_review -v
```

Stop and return the complete output if any test fails.

## 2. Fast review plan

```bash
python tools/temporal_v002_review.py --stage plan
```

Expected: `status=READY`, V002 parity/action statuses remain `PASS`, and
training remains unauthorized.

## 3. Full economic/distribution/support review

```bash
python tools/temporal_v002_review.py --stage audit
```

Expected first-run status:

```text
REVIEW_REQUIRED_SPECIAL_ACTIONS
```

That is intentional. It proves the mechanical audits passed while refusing to
auto-approve special reorganizations.

Return these complete files:

```text
reports/market_temporal_v002_review/plan.json
reports/market_temporal_v002_review/economic_action_review.json
reports/market_temporal_v002_review/target_distribution_report.json
reports/market_temporal_v002_review/support_report.json
reports/market_temporal_v002_review/on_demand_tau_report.json
reports/market_temporal_v002_review/audit.json
reports/market_temporal_v002_review/special_action_decisions_template.json
```

Do not invent decisions just to obtain PASS. The template contains only flagged
events that can enter a model-visible outcome; older lineage-only flags remain
in the action report. We will review the required events and bind evidence to
the generated template.

## 4. Inspect the blocked preregistration plan

It is safe and useful to run this before the special-event decisions close:

```bash
python tools/temporal_distributional_preregistration_v001.py --stage plan
```

Expected until the decision file is finalized:

```text
status   BLOCKED
blocker  V002_ECONOMIC_REVIEW_NOT_CLOSED
```

Return:

```text
reports/temporal_distributional_v001/preregistration_plan.json
reports/temporal_distributional_v001/fold_plan.json
reports/temporal_distributional_v001/audit.json
```

This command counts feasibility only. It has no fitting or prediction code and
does not read holdout performance.

## 5. Later decision-bound rerun

After the generated decisions file has evidence and a disposition for every
flagged `review_id`:

```bash
python tools/temporal_v002_review.py \
  --stage audit \
  --decisions reports/market_temporal_v002_review/special_action_decisions.json

python tools/temporal_distributional_preregistration_v001.py --stage plan
```

`PASS` makes runner implementation eligible. `PASS_WITH_VERSIONED_QUARANTINE`
first requires a separately audited selection-mask implementation and remains
blocked in the current plan. Training remains unauthorized until the frozen
runner is implemented and independently audited.

Never run `dense_all`, alter `market_temporal_v002.db`, edit the decision
fingerprints, or touch V009 to clear a blocker.

## Current checkpoint and complete execution sequence (2026-08-30)

The earlier blocked instructions above are retained as provenance. They have
now been completed. Eleven model-visible special cash events were reconciled
to primary filings/releases and accepted as `validated_cash_and_share_entitlement`.
The economic review, 80-outcome extreme-tail lineage audit, external selection
mask and runner preflight all pass. The audited mask is empty; this is a real
versioned zero-row result, not an absent exclusion mechanism.

Run from the repository root with the project virtual environment active:

```bash
python -m py_compile \
  tools/temporal_v002_tail_audit_v001.py \
  tools/temporal_v002_selection_mask_v001.py \
  models/market/temporal_distributional_v001.py \
  evaluation/market/temporal_distributional_v001.py \
  pipeline/market_temporal_distributional_v001.py

python -m unittest \
  tests.test_temporal_v002_review \
  tests.test_temporal_v002_tail_audit_v001 \
  tests.test_market_temporal_distributional_v001 \
  tests.test_temporal_v002_selection_mask_v001 -v

python tools/temporal_v002_review.py \
  --stage audit \
  --decisions config/evidence/temporal_v002_special_action_decisions_v001.json

python tools/temporal_v002_tail_audit_v001.py --stage audit
python tools/temporal_v002_selection_mask_v001.py --stage all
python tools/temporal_distributional_preregistration_v001.py --stage plan
python pipeline/market_temporal_distributional_v001.py --stage plan
```

All five development folds are resumable and idempotent. Run them sequentially
to avoid multiplying peak RAM:

```bash
for fold in 1 2 3 4 5; do
  python pipeline/market_temporal_distributional_v001.py \
    --stage develop-fold --fold "$fold" || exit 1
done

python pipeline/market_temporal_distributional_v001.py --stage develop-aggregate
python pipeline/market_temporal_distributional_v001.py --stage status
```

Stop unless `development_summary.json` says
`PASS_DEVELOPMENTAL_REQUIRES_FRESH_HOLDOUT`. A fail or inconclusive result is a
scientific terminal result for this version; do not change anchors, features,
model capacity or placebo seeds and rerun.

Only after that exact PASS, freeze every model/prediction/config/code hash:

```bash
python pipeline/market_temporal_distributional_v001.py --stage freeze
```

The next command writes a durable opening marker before reading any sealed
target. It is the one-time prospective boundary:

```bash
for fold in 1 2 3 4 5; do
  python pipeline/market_temporal_distributional_v001.py \
    --stage holdout-fold --fold "$fold" || exit 1
done

python pipeline/market_temporal_distributional_v001.py --stage holdout-aggregate
python pipeline/market_temporal_distributional_v001.py --stage status
```

Return `development_summary.json` after development. If it passes, also return
`development_freeze.json`, `holdout_opening.json` and `holdout_summary.json`.
The source databases, V001, V002 and V009 must remain unchanged.
