from __future__ import annotations
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
FILES=[
"config/market_brain_daily_refresh_v009.json",
"config/market_brain_distributional_v009.json",
"evaluation/market/distributional_v009.py",
"ingestion/prices/yahoo_daily_refresh_v009.py",
"models/market/distributional_v009_prospective.py",
"pipeline/market_brain_daily_refresh_v009.py",
"pipeline/market_brain_distributional_v009.py",
]
TOKENS=["information_capture_orchestrator_v0013","provider_request_observations","scheduled_event_window_observations"]
missing=[]; forbidden=[]
for rel in FILES:
    p=ROOT/rel
    if not p.exists():
        missing.append(rel); continue
    txt=p.read_text(encoding="utf-8",errors="ignore")
    for t in TOKENS:
        if t in txt: forbidden.append({"file":rel,"token":t})
print(json.dumps({
"status":"PASS" if not missing and not forbidden else "FAIL",
"v009_files_missing":missing,
"forbidden_cross_references":forbidden,
"v009_interaction":"NONE"
},indent=2,sort_keys=True))
