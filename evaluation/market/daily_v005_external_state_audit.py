from __future__ import annotations
import json, sqlite3
from pathlib import Path
import numpy as np, pandas as pd

ROOT=Path(__file__).resolve().parents[2]
REF=ROOT/"data"/"processed"/"market_reference_daily_v001.db"
STATE=ROOT/"data"/"processed"/"market_daily_v005_external_state.db"
MATH=ROOT/"data"/"processed"/"market_daily_v004_math.db"
CONFIG=ROOT/"config"/"market_brain_daily_v005_external_state.json"
REPORT=ROOT/"reports"/"market_brain_daily_v005"/"external_state_foundation_audit.json"


def audit(ref=REF,state_db=STATE,math_db=MATH,config=CONFIG):
    cfg=json.loads(Path(config).read_text())
    failures=[]; reviews=[]
    with sqlite3.connect(ref) as c:
        coverage=pd.read_sql_query("""
          SELECT symbol,COUNT(DISTINCT trading_day) rows,
                 MIN(trading_day) first_day,MAX(trading_day) last_day
          FROM reference_daily_observations GROUP BY symbol
        """,c)
    expected=set(cfg["stages"]["market_tradables"]["symbols"])
    if set(coverage.symbol)!=expected: failures.append("reference_symbol_set_mismatch")
    with sqlite3.connect(state_db) as c:
        state=pd.read_sql_query("SELECT * FROM market_external_state_v005",c)
    with sqlite3.connect(math_db) as c:
        market=pd.read_sql_query("SELECT trading_day FROM v004_market_states",c)
    features=cfg["features"]
    finite=np.isfinite(state[features].to_numpy(float)).all(axis=1)
    complete=set(state.loc[finite,"trading_day"].astype(str))
    market_days=set(market.trading_day.astype(str))
    # Focus on V004 modelable range after rolling history is available.
    relevant={d for d in market_days if d>=str(state.loc[finite,"trading_day"].min())} if finite.any() else set()
    overlap=relevant & complete
    ratio=len(overlap)/len(relevant) if relevant else 0.0
    if ratio<0.98: failures.append("v004_market_day_overlap_below_98pct")
    if int(state["point_in_time_verified"].max())!=0:
        failures.append("historical_reference_incorrectly_marked_pit")
    if cfg["strict_historical_pit"] is not False:
        failures.append("strict_pit_contract_changed")
    result={
      "status":"FAIL" if failures else ("REVIEW" if reviews else "PASS"),
      "failures":failures,"reviews":reviews,
      "reference_coverage":coverage.to_dict("records"),
      "state_rows":len(state),
      "complete_feature_rows":int(finite.sum()),
      "v004_relevant_market_days":len(relevant),
      "complete_overlap_days":len(overlap),
      "overlap_fraction":ratio,
      "strict_historical_pit":False,
      "next_gate":"Preregister V005 market-factor enrichment benchmark on V004 folds; do not activate sector/rates/macro yet."
    }
    REPORT.parent.mkdir(parents=True,exist_ok=True)
    REPORT.write_text(json.dumps(result,indent=2),encoding="utf-8")
    return result
