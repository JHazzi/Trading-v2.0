from __future__ import annotations
import json
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
V009 = [
    "config/market_brain_daily_refresh_v009.json",
    "config/market_brain_distributional_v009.json",
    "evaluation/market/distributional_v009.py",
    "ingestion/prices/yahoo_daily_refresh_v009.py",
    "models/market/distributional_v009_prospective.py",
    "pipeline/market_brain_daily_refresh_v009.py",
    "pipeline/market_brain_distributional_v009.py",
]
TOKENS = ["information_capture_scaling_v0011", "alphavantage_scaling_v0011", "expectation_quality_v0011"]
hits=[]; missing=[]
for rel in V009:
    p=ROOT/rel
    if not p.exists(): missing.append(rel); continue
    text=p.read_text(encoding="utf-8", errors="ignore")
    for tok in TOKENS:
        if tok in text: hits.append({"file":rel,"token":tok})
print(json.dumps({"status":"PASS" if not hits and not missing else "FAIL","forbidden_cross_references":hits,"v009_files_missing":missing,"v009_interaction":"NONE"}, indent=2, sort_keys=True))
