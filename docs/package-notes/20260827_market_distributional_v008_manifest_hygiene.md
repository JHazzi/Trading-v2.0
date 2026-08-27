# Market Distributional V008 — pre-performance manifest hygiene

This patch is a **pre-benchmark** amendment. No V008 outer-test performance has been observed.

The V008 schema-only resolver originally admitted three panel-support / data-availability fields into `full_endogenous` because they share the frozen context prefixes:

- `cross_section_peer_count`
- `sector_peer_count`
- `sector_context_missing`

These fields remain valid Core V003 audit/context metadata, but they are not economic state variables we want the predictive learner to exploit. In a current-cohort historical panel they may encode data coverage, universe composition, sector support, or calendar era rather than investable information. They are therefore added to `feature_resolution.explicit_excludes`.

Unchanged: target decomposition, vol63 primary reference, candidate model class/profiles, quantiles, folds, nested temporal selection, 126-day recent calibration, scoring, bootstrap, horizons, feature families, and promotion gates.

After installing this patch, rerun `--stage plan`. The expected `full_endogenous` count falls from 35 to 32, the manifest SHA changes, and `status` must remain `PASS`. Freeze the regenerated `resolved_feature_manifest.json` and `preregistration.json` in Git before the first benchmark.
