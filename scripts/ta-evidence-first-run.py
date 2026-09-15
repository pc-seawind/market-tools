#!/usr/bin/env python3
"""Explicit code-domain acceptance run: all frozen holdings, CATL model first.
Use systemd transient; does not promote production pointer or write investment.
"""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from mt1.ta_research import run,SCOPE,save,now
from mt1.ta_audit import audit
from mt1.ta_pipeline import CATALOG
from mt1.ta_evidence import collect
p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--report',required=True);p.add_argument('--collection',required=True);a=p.parse_args()
r=run(a.root,a.report,SCOPE,CATALOG,research_collection=a.collection,model_codes=['300750.SZ'])
save(Path(a.root)/'audit.json',audit(r['manifest']))
print(json.dumps(r,ensure_ascii=False),flush=True)
