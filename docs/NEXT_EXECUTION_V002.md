# Next Execution V002 — Total Return Materialization

These commands build data and audits only. They do not train a model, mutate
the source/Core/V001 databases or touch V009.

## 1. Verify code and synthetic gates

From `/home/trabajo/quant_market_ai` with the existing environment active:

```bash
python -m py_compile \
  tools/temporal_dataset_v001.py \
  tools/temporal_dataset_v002.py

python -m unittest \
  tests.test_temporal_dataset_v001 \
  tests.test_temporal_dataset_v002 -v
```

Expected: all tests pass. Stop and return the complete output if any test
fails.

## 2. Run the read-only real plan

```bash
python tools/temporal_dataset_v002.py --stage plan
```

Expected high-level values:

```text
status                         READY
assets                         497
origin_rows                    1,092,555
materialized_tau_count         17
estimated_outcome_rows         18,573,435
split_factor_is_applied        false
provider_adjusted_close_role   audit_only_not_target
training_authorized            false
```

Do not continue if the plan is blocked or the counts differ unexpectedly.

## 3. Build the sparse V002 artifact

```bash
python tools/temporal_dataset_v002.py --stage build
```

The build is resumable only through an identical completed artifact; partial
temporary databases are not published. It may take time because it checks all
V001 materialized outcomes and reconstructs 18.6 million total-return rows.

Do not interrupt merely because progress pauses between asset checkpoints.
Do not run another builder against the same output concurrently.

## 4. Replay the independent audit

```bash
python tools/temporal_dataset_v002.py --stage audit
```

Expected gates before scientific review:

```text
integrity_status          PASS
v001_parity.status        PASS
no_action_identity.status PASS
action_reconciliation     PASS
training_gate_status      BLOCKED_PENDING_V002_FULL_ACTION_REVIEW
training_authorized       false
model_training_performed  false
v009_loaded_or_modified   false
```

The blocked training status is expected. A data `PASS` does not authorize a
model.

## 5. Return these reports

Return the complete files:

```text
reports/market_temporal_v002/plan.json
reports/market_temporal_v002/v001_parity_report.json
reports/market_temporal_v002/no_action_identity_report.json
reports/market_temporal_v002/action_reconciliation_report.json
reports/market_temporal_v002/coverage_report.json
reports/market_temporal_v002/audit.json
```

Do not send or commit:

```text
data/processed/market_temporal_v002.db
```

The database is a large local derived artifact. Preserve it locally after a
successful build because an identical rerun should reuse it.

## Failure handling

If build/audit fails:

1. do not run training;
2. do not alter source/Core/V001;
3. do not use `--force-rebuild` as a workaround;
4. return the full terminal output and every V002 report that was written.

`--force-rebuild` is only for an intentional, reviewed contract/input/code
change after the existing artifact fingerprint no longer matches.

Do not run this unless a later preregistered method truly requires it:

```bash
python tools/temporal_dataset_v002.py --stage plan --strategy dense_all
```

The dense plan is approximately 14.8 times larger and does not create
independent paths.
