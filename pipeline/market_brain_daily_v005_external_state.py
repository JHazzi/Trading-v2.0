from __future__ import annotations
import argparse,json
from ingestion.market_reference.yahoo_reference_daily_v001 import acquire
from features.market.daily_v005_external_state import build
from evaluation.market.daily_v005_external_state_audit import audit

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--stage",required=True,choices=("acquire","build","audit"))
    a=p.parse_args()
    if a.stage=="acquire": x=acquire()
    elif a.stage=="build": x=build()
    else: x=audit()
    print(json.dumps(x,indent=2))
if __name__=="__main__": main()
