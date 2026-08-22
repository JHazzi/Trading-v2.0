from __future__ import annotations

import argparse
from pathlib import Path

if __package__ in (None, ""):
    from pathlib import Path
    import sys

    sys.path.insert(
        0,
        str(Path(__file__).resolve().parents[2])
    )

try:
    from models.market.dataset import FEATURES, load_supervised_dataset
except ImportError:
    from ...models.market.dataset import FEATURES, load_supervised_dataset

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "database" / "market_data_v2.db"


def build(horizon_seconds: int, min_coverage: float = 95.0):
    df = load_supervised_dataset(DB_PATH, horizon_seconds, min_coverage)
    return df


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--horizon", type=int, required=True)
    parser.add_argument("--min-coverage", type=float, default=95.0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    df = build(args.horizon, args.min_coverage)
    payload = {
        "rows": int(len(df)),
        "columns": list(df.columns),
        "feature_count": len(FEATURES),
        "horizon_seconds": args.horizon,
        "min_coverage": args.min_coverage,
    }
    if args.json:
        import json
        print(json.dumps(payload, indent=2))
    else:
        print(payload)


if __name__ == "__main__":
    main()
