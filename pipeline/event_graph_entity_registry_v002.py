from __future__ import annotations
import argparse,json
from pathlib import Path
from knowledge.entities.entity_registry_v002 import (
    DEFAULT_CONFIG,plan,build
)
from evaluation.events.entity_registry_v002_audit import audit

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--config",type=Path,default=DEFAULT_CONFIG)
    p.add_argument("--stage",required=True,choices=("plan","build","audit"))
    a=p.parse_args()
    if a.stage=="plan": r=plan(a.config)
    elif a.stage=="build": r=build(a.config)
    else: r=audit(a.config)
    print(json.dumps(r,indent=2))
if __name__=="__main__":main()
