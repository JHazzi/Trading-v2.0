from __future__ import annotations
import argparse,json
from pathlib import Path
from knowledge.relations.relation_evidence_extraction_v002 import (
    DEFAULT_CONFIG,plan,extract,qa_sample
)
from evaluation.events.relation_evidence_audit_v002 import audit

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--config",type=Path,default=DEFAULT_CONFIG)
    p.add_argument("--stage",required=True,choices=("plan","extract","audit","qa-sample"))
    a=p.parse_args()
    if a.stage=="plan": r=plan(a.config)
    elif a.stage=="extract": r=extract(a.config)
    elif a.stage=="audit": r=audit(a.config)
    else: r=qa_sample(a.config)
    print(json.dumps(r,indent=2))
if __name__=="__main__": main()
