# Market Distributional V008 v0011 — split-feasibility amendment

## Why this amendment exists
The first H1 benchmark attempt of V008 v001 aborted in `split_recent_days` before any HGB model was fit and before any OOS performance statistic was produced. With `initial_fraction=0.30`, the earliest outer fold does not have enough purged origin-day support for three disjoint temporal blocks when the original minimum inner training requirement is 500 days: 126 calibration + 126 nested validation + 500 nested training = 752 days, before horizon purges.

## Frozen change
`minimum_inner_train_origin_days` changes from 500 to 378 origin days (1.5 trading years) for profile selection only. This is sufficient for selecting between only two preregistered HGB capacity profiles while preserving the 126-day validation and 126-day calibration windows. The final selected HGB models continue to fit on the entire development block, not only the minimum inner-training subset.

Everything else remains frozen: features/manifest hygiene, target decomposition, horizons, 30% initial outer split, five purged expanding folds, model profiles, 126-day recent calibration, primary reference, metrics, bootstrap, gates and anti-selection rules.

## Added prevention
`--stage plan` now performs a clock-only conservative temporal-split feasibility audit for each horizon. It reads origin/target day support but no return outcomes and computes no model score.

## Interpretation boundary
This is a pre-performance implementation/preregistration amendment. It must not be interpreted as tuning to V008 results because no V008 model performance had been produced before the change.
