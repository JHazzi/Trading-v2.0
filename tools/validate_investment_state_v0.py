from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from product.workbench_v0.state_contract import load_state


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("state")
    args = parser.parse_args()
    validated = load_state(args.state)
    print("PASS")
    print(f"ticker={validated.payload['asset']['ticker']}")
    print(f"snapshot_sha256={validated.sha256}")


if __name__ == "__main__":
    main()
