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
