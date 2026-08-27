from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

BLOCKS = {
    ROOT / "docs" / "EXPERIMENTS.md": (
        "MARKET_DIST_V007_ADAPTIVE_TAIL_V001",
        """## E-MARKET-DIST-V0061 — completed robustness interpretation

**Status:** complete; source V006 reproduced exactly on all horizons.

V006.1 supports the broad conditional-dispersion claim but narrows its functional form:
- leave-one-asset and leave-one-sector deltas remain positive at every horizon, so the V006 vs global gain is not driven by one asset or one sector;
- `asset_vol_5d_pct` is decisively worse than V006 at all horizons;
- `asset_vol_63d_pct` is a stronger sensitivity than V006 at H3/H5/H10 and directionally stronger at H1;
- the benefit is asymmetric across tails: the upper tail improves strongly while q05/q25 deteriorate at longer horizons;
- low-volatility regimes are under-covered and high-volatility regimes are over-covered, consistent with an overly linear scale response;
- `asset_empirical` remains a serious structural reference, especially at longer horizons.

These findings do not retroactively replace V006. They define the preregistered hypothesis for V007.

## E-MARKET-DIST-V007 — adaptive asymmetric asset-scale preregistration

**Status:** preregistered; no V007 performance interpreted yet.

V007 keeps q50 at the global training median and learns only distribution shape/scale. For each outer fold it uses a nested temporal validation split to select separate downside and upside parameters. The asset supplies a structural tail anchor; current 20d/63d volatility supplies a normalized dynamic state.

For side `s` in {downside, upside}:

```text
u_i,t = lambda20 * log(vol20_i,t / median_train_i(vol20))
      + (1-lambda20) * log(vol63_i,t / median_train_i(vol63))

g_s(i,t) = kappa_s * exp(alpha_s * u_i,t)
```

For q<0.5 or q>0.5:

```text
Q_q(i,t) = global_train_median
         + (asset_train_Q_q - asset_train_Q_50) * g_s(i,t)
```

q50 is never dynamically learned in V007.

Nested selection minimizes origin-day-equal pinball on q05/q25 and q75/q95 separately. Primary outer reference is the predeclared `vol63_scaled_empirical`; V006 `vol20`, `asset_empirical` and global empirical remain secondary controls. All four horizons must be reported.
""",
    ),
    ROOT / "docs" / "RESEARCH_STATUS.md": (
        "MARKET_DIST_V007_ADAPTIVE_TAIL_V001",
        """## Distributional V006.1 closed; V007 adaptive-tail model active

V006.1 reproduced the completed V006 result on all H1/H3/H5/H10 samples and did not find asset/sector concentration capable of removing the positive V006-vs-global result. It also narrowed the claim: V006 is directionally asymmetric, has regime-dependent calibration error, and a 63-session volatility sensitivity is stronger than vol20 at H3/H5/H10. The global point result is therefore real enough to escalate, but the V006 linear symmetric scale formula is not treated as a final model.

Active next experiment: `market_brain_distributional_v007_adaptive_tail_v001`. It is a low-dimensional learned/semi-parametric distributional model with nested temporal selection, asset-specific structural tail anchors, separate downside/upside dynamic scales, and no learned location. `vol63_scaled_empirical` is the new preregistered primary reference; V006, asset empirical and global empirical remain controls.

V007 is developmental evidence because its hypothesis was informed by V006.1 outcomes. It is not independent prospective confirmation, not a path model and not production-ready.
""",
    ),
    ROOT / "docs" / "RESEARCH_DECISIONS.md": (
        "MARKET_DIST_V007_ADAPTIVE_TAIL_V001",
        """## Decision — learn shape/scale before direction

V006.1 showed three coherent facts: longer volatility memory outperforms short memory, a linear scale response miscalibrates low/high volatility regimes in opposite directions, and upside/downside tails behave differently. Therefore the next learned Market Brain will not add directional features or a generic black-box model.

V007 freezes the location at the global training median and learns only tail geometry. It combines an asset-specific empirical tail anchor with a train-normalized blend of vol20 and vol63, allowing separate downside/upside `alpha`, `lambda20` and `kappa` selected only inside each outer training period. This is deliberately more interpretable than jumping directly to a large quantile booster.

The strongest simple V006.1 sensitivity, `vol63_scaled_empirical`, becomes V007's primary reference prospectively. This does not rewrite the completed V006 primary.
""",
    ),
    ROOT / "docs" / "ROADMAP.md": (
        "MARKET_DIST_V007_ADAPTIVE_TAIL_V001",
        """## Learned Distributional Market Brain — V007 active

V006.1 robustness is complete. The next active experiment is V007 adaptive asymmetric asset-scale.

Required design:
- reuse the Core V003 causal daily panel and V003 outer purged folds;
- nested temporal selection inside each outer train;
- q50 fixed to global train median, so no directional-location claim enters this increment;
- asset-specific empirical tail shape as structural anchor;
- dynamic state limited to causal vol20 and vol63 normalized by each asset's training medians;
- separate downside/upside scale parameters;
- primary comparison against `vol63_scaled_empirical`;
- secondary comparisons against V006 vol20, `asset_empirical` and global empirical;
- pinball plus quantile calibration primary diagnostics, Brier/median MAE still reported;
- all four horizons mandatory.

A strong V007 result can justify a richer learned quantile model later. It cannot by itself count as prospective confirmation because V006.1 informed the mathematical hypothesis.
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
