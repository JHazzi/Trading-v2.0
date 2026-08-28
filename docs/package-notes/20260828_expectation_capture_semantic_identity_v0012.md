# Expectation Capture Semantic Identity V0012

This patch is a pre-scaling semantic correction.

## Finding

The first live Alpha Vantage snapshot produced 287 normalized expectation rows for
each of ten symbols. The structure is consistent with 41 provider estimate-period
rows per symbol, each normalized into seven observables:

- EPS average/high/low;
- revenue average/high/low;
- EPS analyst count.

The V0011 diagnostic also found same-snapshot collisions under the coarse identity:

`entity + expectation_type + metric + fiscal_period + statistic`.

Inspection shows that fiscal-quarter and fiscal-year estimates can share the same
`fiscalDateEnding`. They are economically distinct series, not revisions.

## Decision

V0012 adds `period_scope` to canonical series identity:

`expectation_type + period_scope + metric + fiscal_period + statistic`.

No existing observation is mutated. Existing rows recover scope from
`metadata_json.provider_horizon`; future Alpha Vantage rows also persist normalized
`metadata_json.period_scope`.

## Research integrity

This patch occurs before a second live expectation snapshot is used for revision
measurement. It does not change V009, Market Core, labels, predictor features, or
any trained artifact.

Provider enablement is acquisition configuration only. The old test requiring
`alpha_vantage.enabled == false` was therefore incorrect once live capture began;
the invariant is that feature visibility remains blocked.

## Scaling

Under a standard 20-request/day research budget, a daily 10-symbol deep cohort
leaves nominal capacity for ten broad symbols/day: one 487-symbol broad rotation
takes about 49 days before earnings-priority displacement. Therefore V0012 changes
the default broad cadence from 7 to 60 days. Higher cadence requires an explicitly
verified higher provider entitlement.
