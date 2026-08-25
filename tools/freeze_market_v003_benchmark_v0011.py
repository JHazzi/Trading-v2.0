from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
from pathlib import Path

import numpy
import pandas
import sklearn

ROOT = Path(__file__).resolve().parents[1]
FILES = [
    ROOT / "config" / "market_brain_daily_v003_benchmark.json",
    ROOT / "evaluation" / "market" / "daily_v003_benchmark.py",
    ROOT / "models" / "market" / "daily_v003_benchmark.py",
    ROOT / "pipeline" / "market_brain_daily_benchmark_v003.py",
    ROOT / "features" / "market" / "daily_v003_core.py",
    ROOT / "evaluation" / "market" / "daily_v003_core_audit.py",
]
CORE_DB = ROOT / "data" / "processed" / "market_daily_v003_core.db"
OUT = (
    ROOT / "reports" / "market_brain_daily_v003" / "benchmark_v0011"
    / "preregistered_inputs.json"
)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def git(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=ROOT, text=True
    ).strip()


def main() -> None:
    missing = [str(p) for p in [*FILES, CORE_DB] if not p.is_file()]
    if missing:
        raise FileNotFoundError(missing)

    status = git("status", "--porcelain")
    result = {
        "benchmark_version": "market_brain_daily_v003_benchmark_v0011",
        "git": {
            "head": git("rev-parse", "HEAD"),
            "branch": git("branch", "--show-current"),
            "working_tree_clean": status == "",
            "status_porcelain": status.splitlines(),
        },
        "environment": {
            "python": sys.version,
            "platform": platform.platform(),
            "numpy": numpy.__version__,
            "pandas": pandas.__version__,
            "scikit_learn": sklearn.__version__,
        },
        "sha256": {str(p.relative_to(ROOT)): sha256(p) for p in FILES},
        "core_db": {
            "path": str(CORE_DB.relative_to(ROOT)),
            "size_bytes": CORE_DB.stat().st_size,
            "sha256": sha256(CORE_DB),
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
