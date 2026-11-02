#!/usr/bin/env python3
"""Disjoint seeded synthetic task sequences; durable per-seed checkpoints.

Uses only stdlib/SQLite, no models, network, production files or GPU allocation.
Coordinator assigns disjoint seed ranges to owned CPU hosts, publishes receipts.
"""
import argparse
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import random
import socket
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mt1.research_tasks import apply, snapshot, timeline
from mt1.research_pool import apply as research_apply

FILES = ['mt1/__init__.py', 'mt1/store.py', 'mt1/research_pool.py', 'mt1/research_tasks.py',
         'mt1/research_performance.py', 'scripts/verify_research_task_matrix.py']


def run_seed(root, seed):
    rng = random.Random(seed)
    now = datetime(2026, 11, 2, 8, tzinfo=timezone.utc)
    scope = root / 'scope.json'
    raw = json.dumps({'scope_epoch': 'synthetic-matrix', 'reset_at': (now-timedelta(days=2)).isoformat(),
                      'confirmed_holdings': [], 'active_candidates': []}).encode()
    scope.write_bytes(raw)
    common = dict(code=f'SYNTHETIC-{seed}.SZ', scope_epoch='synthetic-matrix', domain='investment', reviewer='synthetic-matrix',
                  scope_sha256=hashlib.sha256(raw).hexdigest(), reviewed_at=now.isoformat(), reason='synthetic matrix')
    def event(action, **kw):
        rows = snapshot(scope,common['scope_epoch'],now)['rows']
        t = rows[0] if rows else dict(version=0,research_version=0)
        p = dict(common,action=action,request_id=f'{seed}-{action}-{t["version"]}',
                 expected_version=t['version'],expected_research_version=t['research_version'])
        p.update(kw)
        r = apply(scope,p,now)
        assert apply(scope,p,now)==r  # exact retry is immutable/idempotent
        return r
    due=(now+timedelta(days=2+rng.randrange(3))).isoformat()
    event('start',open_gaps=['cash','valuation'],next_action='synthetic cash check',due=due)
    for n in range(2):
        event('attempt',reason=f'same evidence, text refresh {n}')
    t=snapshot(scope,common['scope_epoch'],now)['rows'][0]
    assert t['status']=='review_required' and t['due']==t['original_due']==due
    decision=rng.choice(['wait','block','archive'])
    extra=dict(trigger_key='filing',trigger_condition='new filing',check_at=(now+timedelta(days=1)).isoformat()) if decision!='archive' else {}
    event('review',decision=decision,disposition_basis='synthetic priority/coverage decision',next_action='new filing explicit reopen',**extra)
    assert snapshot(scope,common['scope_epoch'],now)['accounting']['closing_active']==0
    source=root/'source.txt';source.write_text(f'SYNTHETIC new filing seed {seed} nonce {rng.random()}')
    src=dict(path=str(source),sha256=hashlib.sha256(source.read_bytes()).hexdigest(),url='https://example.invalid/SYNTHETIC',
             published_at=now.isoformat(),fetched_at=now.isoformat())
    event('reopen',open_gaps=['valuation'],next_action='synthetic valuation check',due=due,sources=[src],trigger_key='filing' if extra else None)
    # A real admission is the only completion entry; original sources/signoff contract applies.
    p=dict(common,state='watch',request_id=f'{seed}-admission',expected_version=0,
           business_logic='SYNTHETIC company logic',risk='SYNTHETIC risk',sources=[src],next_review=due,
           milestones=[dict(description='synthetic future node',at=(now+timedelta(days=30)).isoformat())])
    research_apply(scope,p,now)
    snap=snapshot(scope,common['scope_epoch'],now,now-timedelta(seconds=1))
    assert snap['accounting']['reconciled'] and snap['accounting']['closing_active']==0
    assert snap['accounting']['started']==1 and snap['accounting']['reopened']==1 and snap['accounting']['completed']==1
    assert snap['rows'][0]['status']=='completed' and snap['rows'][0]['generation']==2
    assert scope.read_bytes()==raw
    history=timeline(scope,common['scope_epoch'])
    (root/'events.json').write_text(json.dumps(history,ensure_ascii=False,indent=2))
    return dict(seed=seed,host=socket.gethostname(),status='passed',decision=decision,events=len(history),
                accounting=snap['accounting'],scope_sha256=hashlib.sha256(raw).hexdigest())


def main():
    p=argparse.ArgumentParser();p.add_argument('--seeds',required=True);p.add_argument('--out',required=True);a=p.parse_args()
    root=Path(a.out);root.mkdir(parents=True,exist_ok=True)
    pins={n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in FILES}
    manifest=root/'pins.json'
    if manifest.exists():
        assert json.loads(manifest.read_text())==pins
    else:
        manifest.write_text(json.dumps(pins,sort_keys=True))
    results=[]
    for seed in map(int,a.seeds.split(',')):
        dest=root/str(seed);dest.mkdir(exist_ok=True);cp=dest/'checkpoint.json'
        if cp.exists():
            result=json.loads(cp.read_text());assert result['seed']==seed and result['host']==socket.gethostname()
        else:
            result=run_seed(dest,seed);cp.write_text(json.dumps(result,ensure_ascii=False,indent=2))
        results.append(result)
    result=dict(host=socket.gethostname(),status='passed',seeds=[r['seed'] for r in results],items=results,code_hashes=pins,
                models=0,gpu_leases=0,synthetic_only=True)
    (root/'receipt.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':
    main()
