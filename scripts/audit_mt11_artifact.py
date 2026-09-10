#!/usr/bin/env python3
"""Independent artifact conservation/hash audit; NOT strategy acceptance."""
import argparse
import csv
import gzip
import hashlib
import json
from collections import Counter
from pathlib import Path


def audit(root):
    root=Path(root);s=json.loads((root/'summary.json').read_text())
    for path,h in s['frozen_hashes'].items():
        assert hashlib.sha256((root/path).read_bytes()).hexdigest()==h, path
    with gzip.open(root/'funnel.json.gz','rt') as f:records=json.load(f)
    assert len(records)==s['universe']==len({r['code'] for r in records})
    with (root/'stages.csv').open() as f:rows=list(csv.DictReader(f))
    assert len(rows)==s['universe']*3
    pool=root/'discovery-pool.jsonl'
    queue=[json.loads(line) for line in (pool if pool.exists() else root/'research-queue.jsonl').read_text().splitlines()]
    if pool.exists():
        advancing=[json.loads(line) for line in (root/'research-queue.jsonl').read_text().splitlines()]
        assert all(not any(v['status']=='reject' for v in r['channel_risk'].values()) for r in advancing)
        assert len(advancing)==s['advancing_research_count']
        assert s['research_batch_codes']==[r['code'] for r in advancing[:10]]
    discovered={r['code'] for r in records if r['channels']}
    assert len(queue)==len(discovered)==s['deduplicated']
    assert {r['code'] for r in queue}==discovered
    assert [r['research_rank'] for r in queue]==list(range(1,len(queue)+1))
    for ch,stats in s['channels'].items():
        counts=Counter(r['discovery'][ch]['status'] for r in records)
        assert dict(counts)==stats['discovery'] and sum(counts.values())==s['universe']
        selected=[r for r in records if ch in r['channels']]
        assert len(selected)==counts['pass']
        assert dict(Counter(r['board'] for r in selected))==stats['discovery_boards']
        assert sum(stats['risk'].values())==len(selected)
        assert sum(stats['timing_diagnostic'].values())==len(selected)
    assert all(r['plan']['qualification']=='pending_review' and not r['plan']['final_buy'] for r in queue)
    with gzip.open(root/'price-input.json.gz','rt') as f:prices=json.load(f)
    assert prices['sessions'][-1]==s['asof'] and len(prices['sessions'])==120
    result={'engineering_audit':'passed','strategy_acceptance':'not_assessed','asof':s['asof'],'universe':s['universe'],'deduplicated':len(queue),'stage_rows':len(rows),'frozen_hashes_checked':len(s['frozen_hashes']),'final_buy':0}
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('root');a=p.parse_args()
    print(json.dumps(audit(a.root),ensure_ascii=False,indent=2))
