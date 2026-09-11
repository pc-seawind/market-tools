"""Reproduce public-source acquisition from a sealed URL plan, checkpoints reused.
The plan contains public URL requests only. No credential endpoints or purchase.
Use --out r3 to check existing raw, or a new sibling directory to collect a new
vintage (which must NOT silently replace the frozen r3 research inputs).
"""
import argparse
import json
from pathlib import Path
from urllib.parse import urlparse
from .hk_free_probe import fetch
from .history_research import write

ALLOWED={'www1.hkexnews.hk','www.hkex.com.hk','www.hkexgroup.com','en-rules.hkex.com.hk','www.ird.gov.hk','finance.sina.com.cn','r.jina.ai'}


def collect(plan,out,offline=False):
    raw=out/'raw';raw.mkdir(parents=True,exist_ok=True)
    records=[]
    for r in json.loads(plan.read_text()):
        if urlparse(r['url']).hostname not in ALLOWED:raise ValueError('unapproved_public_source')
        records.append(fetch(raw,r['name'],r['url'],offline))
        # Persist after every request, including failed HTTP responses; no retry storm.
        write(out/'public-collection-status.json',records)
    return records


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--plan',type=Path,required=True);ap.add_argument('--out',type=Path,required=True);ap.add_argument('--offline',action='store_true');a=ap.parse_args()
    if a.out.resolve().name in ('mt12-history-20260912','mt12-history-20260912-r2'):raise ValueError('protected_output')
    collect(a.plan,a.out,a.offline)
