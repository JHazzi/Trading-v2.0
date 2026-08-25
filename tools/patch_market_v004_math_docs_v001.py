from __future__ import annotations
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKER = "<!-- MARKET_V004_MATH_FOUNDATION_V001 -->"

STATUS = r"""

<!-- MARKET_V004_MATH_FOUNDATION_V001 -->
## Market Brain Daily V004 mathematical foundation

V003 remains a canonical negative benchmark. It is not deleted or rewritten.

The next Market Brain candidate remains inside Architecture Phase C. V004 tests
whether a hierarchical/factorized target representation improves temporal
generalization before adding external information or distributional outputs.

V004 separates statistical units:

```text
market: one row per origin day
sector: one row per sector-day
asset:  one row per asset-day
```

It materializes two target decompositions:

```text
additive:
R_i = M + S + E_i

dynamic factor:
R_i = beta_i,t * M + gamma_i,t * S + alpha_i
```

`beta` and `gamma` are estimated only from observations available through the
origin close. Neither factorization is assumed correct until walk-forward
evaluation supports it.

After the mathematical factorization gate, external Market State information
will be added incrementally: market ETFs, sector ETFs, volatility,
rates/credit, then vintage-causal macro. Events remain deferred until the base
Market Brain shows skill.
"""

DECISION = r"""

<!-- MARKET_V004_MATH_FOUNDATION_V001 -->
## Decision — preserve V003 and expand Market Brain information carefully

V003 answered a deliberately narrow question: price/volume/relative state
alone, pooled at asset-day level, did not beat the preregistered absolute
return baseline.

This does not reject the project objective
`P(R[t:t+T] | X_t, E_t, G_t, T)`.

Decision:

1. retain all V003 artifacts/results as negative evidence;
2. test factorized mathematical targets without new external data;
3. if factorized components generalize, add external market-wide state
   incrementally to the statistically appropriate level;
4. require causal `available_at <= t` contracts for every enrichment;
5. do not tune V003 after observing its failure;
6. do not integrate Event Brain or distributional heads before a credible
   point-estimate Market Brain baseline exists.
"""

ROADMAP = r"""

<!-- MARKET_V004_MATH_FOUNDATION_V001 -->
### Phase C refinement — Market Brain

```text
C1 V003 endogenous pooled baseline           REJECTED, retained
C2 V004 mathematical factorization           ACTIVE
C3 external market-state increments          NEXT if C2 is healthy
   - SPY / QQQ / IWM
   - sector ETFs
   - volatility
   - rates / credit
   - causal macro / regime
C4 distributional Market Brain               DEFERRED
D  Event Brain integration                    DEFERRED
```

C2 must separately evaluate market-factor, sector-residual, asset-residual
and reconstructed absolute-return performance. A component is kept only if it
adds paired out-of-sample value.
"""


def append_once(path: Path, block: str, apply: bool) -> str:
    if not path.is_file():
        raise FileNotFoundError(path)
    content = path.read_text(encoding="utf-8")
    if MARKER in content:
        return "already_applied"
    if apply:
        path.write_text(content.rstrip() + "\n" + block.lstrip(), encoding="utf-8")
        return "applied"
    return "would_apply"


def main():
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true")
    g.add_argument("--apply", action="store_true")
    a = p.parse_args()
    for rel, block in (
        ("docs/RESEARCH_STATUS.md", STATUS),
        ("docs/RESEARCH_DECISIONS.md", DECISION),
        ("docs/ROADMAP.md", ROADMAP),
    ):
        print(f"{append_once(ROOT/rel, block, a.apply):15s} {rel}")


if __name__ == "__main__":
    main()
