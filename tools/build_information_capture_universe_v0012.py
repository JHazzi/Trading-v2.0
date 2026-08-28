from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DEFAULT_MANIFEST = Path("reports/market_brain_distributional_v009/prospective_holdout_v001/universe_manifest.json")
DEFAULT_OUTPUT = Path("data/information_capture_universe_v001.txt")


def _clean_symbol(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    s = value.strip().upper()
    if not s or len(s) > 12 or " " in s or "/" in s:
        return None
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-")
    if any(ch not in allowed for ch in s):
        return None
    return s


def extract_symbols(obj: Any, parent_key: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(obj, dict):
        # Direct ticker-bearing objects.
        for key in ("ticker", "symbol", "asset_ticker"):
            if key in obj:
                s = _clean_symbol(obj[key])
                if s:
                    found.append(s)
        for key, value in obj.items():
            k = str(key).lower()
            if isinstance(value, list) and any(token in k for token in ("symbol", "ticker", "universe", "asset", "member")):
                for item in value:
                    if isinstance(item, str):
                        s = _clean_symbol(item)
                        if s:
                            found.append(s)
                    else:
                        found.extend(extract_symbols(item, k))
            elif isinstance(value, (dict, list)):
                found.extend(extract_symbols(value, k))
    elif isinstance(obj, list):
        for item in obj:
            found.extend(extract_symbols(item, parent_key))
    return found


def main() -> None:
    ap = argparse.ArgumentParser(description="Build expectation-capture universe from the frozen V009 universe manifest.")
    ap.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    ap.add_argument("--output", default=str(DEFAULT_OUTPUT))
    ap.add_argument("--expected-count", type=int, default=497)
    ap.add_argument("--allow-count-mismatch", action="store_true")
    args = ap.parse_args()

    manifest = Path(args.manifest)
    if not manifest.exists():
        raise SystemExit(f"manifest not found: {manifest}")
    obj = json.loads(manifest.read_text(encoding="utf-8"))
    raw = extract_symbols(obj)
    symbols = []
    seen = set()
    for s in raw:
        if s not in seen:
            seen.add(s)
            symbols.append(s)
    symbols.sort()

    if len(symbols) != args.expected_count and not args.allow_count_mismatch:
        raise SystemExit(
            f"extracted {len(symbols)} unique symbols, expected {args.expected_count}; "
            "refusing to write a potentially wrong capture universe"
        )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(symbols) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": "PASS",
        "manifest": str(manifest),
        "output": str(output),
        "symbols": len(symbols),
        "first_symbols": symbols[:10],
        "last_symbols": symbols[-10:],
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
