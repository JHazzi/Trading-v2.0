from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

BLOCKS = {
    ROOT / "docs" / "EXPERIMENTS.md": (
        "MARKET_DIST_V0061_ROBUSTNESS_V001",
        """## E-MARKET-DIST-V0061 — robustness/falsification preregistration

**Status:** preregistered; results intentionally not yet interpreted.

V006 remains the completed primary. V006.1 does not change its model, target, quantiles, folds, primary unit or claim. It must first reproduce the frozen V006 OOS daily losses and fail if reproduction differs beyond the configured numerical tolerance.

Predeclared diagnostics:
- fold and quantile-specific pinball/calibration;
- direct V006 vs `asset_empirical` comparison;
- asset and sector contribution concentration plus leave-one-group-out sensitivity;
- low/mid/high volatility regimes defined only from each outer fold's training `asset_vol_20d_pct`;
- non-overlapping 126-origin-day calibration blocks within each outer fold;
- alternative causal scale sensitivities using `asset_vol_5d_pct` and `asset_vol_63d_pct`;
- moving-block bootstrap on origin-day losses at 5/10/20 days where a comparison is inferentially summarized.

Alternative scales are diagnostics only and cannot retroactively replace the V006 `vol20` primary. No event, graph, macro, external proxy, cost, path or new learned-model feature enters V006.1.
""",
    ),
    ROOT / "docs" / "RESEARCH_STATUS.md": (
        "MARKET_DIST_V0061_ROBUSTNESS_V001",
        """## V006.1 robustness/falsification — preregistered

The completed V006 conditional-dispersion result remains unchanged. The next active experiment is a diagnostics-only attempt to falsify or narrow that claim by exact source reproduction, tail analysis, asset/sector concentration, train-defined volatility regimes, calibration drift, direct comparison with the `asset_empirical` secondary reference and predeclared `vol5`/`vol63` scale sensitivities.

No V006.1 diagnostic may be used to retroactively select a replacement primary specification. A learned distributional Market Brain remains blocked until V006.1 is interpreted.
""",
    ),
    ROOT / "docs" / "RESEARCH_DECISIONS.md": (
        "MARKET_DIST_V0061_ROBUSTNESS_V001",
        """## Decision — freeze V006.1 as falsification, not optimization

V006.1 preserves the completed V006 primary and asks where its conditional-dispersion claim does or does not hold. The experiment must reproduce frozen V006 daily OOS losses before any subgroup diagnostic is accepted. `asset_vol_5d_pct` and `asset_vol_63d_pct` are predeclared sensitivity scales only; neither can become the new primary from V006.1 results. The learned distributional model will receive its own version, preregistration and temporal selection design.
""",
    ),
    ROOT / "docs" / "ROADMAP.md": (
        "MARKET_DIST_V0061_ROBUSTNESS_V001",
        """## V006.1 execution contract

Before learned distributional modeling, run the frozen V006.1 robustness package. Required outputs are exact V006 reproduction, tail-specific diagnostics, direct `asset_empirical` comparison, asset/sector concentration and leave-one-out sensitivity, train-defined volatility regimes, calibration drift blocks, and predeclared `vol5`/`vol63` scale sensitivities.

V006.1 has no promotion gate for an alternative scale. Its role is to determine the scope and failure modes of the existing V006 claim. Only after interpreting all four horizons should a separately versioned learned distributional Market Brain be preregistered.
""",
    ),
}


def _marker(key: str, suffix: str) -> str:
    return f"<!-- {key}_{suffix} -->"


def upsert(text: str, key: str, body: str) -> str:
    start = _marker(key, "START")
    end = _marker(key, "END")
    block = f"{start}\n{body.rstrip()}\n{end}"
    if start in text and end in text:
        left = text.index(start)
        right = text.index(end, left) + len(end)
        return text[:left] + block + text[right:]
    if not text.endswith("\n"):
        text += "\n"
    return text + "\n" + block + "\n"


def build_changes() -> dict[Path, str]:
    changes = {}
    for path, (key, body) in BLOCKS.items():
        if not path.exists():
            raise FileNotFoundError(path)
        original = path.read_text(encoding="utf-8")
        changes[path] = upsert(original, key, body)
    return changes


def main() -> None:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    changes = build_changes()
    changed = []
    for path, updated in changes.items():
        original = path.read_text(encoding="utf-8")
        if original != updated:
            changed.append(str(path.relative_to(ROOT)))
            if args.apply:
                path.write_text(updated, encoding="utf-8")
    print("mode:", "apply" if args.apply else "check")
    print("files_changed:", len(changed))
    for path in changed:
        print(" -", path)


if __name__ == "__main__":
    main()
