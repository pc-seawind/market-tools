"""Bounded, checkpointed CURRENT shadow panel; never historical PIT certification."""
import argparse
import json
import time
from pathlib import Path
from datetime import datetime, timedelta, timezone
from .data import api, atomic_json


def collect(root, max_requests=250, max_seconds=3600):
    root=Path(root); root.mkdir(parents=True,exist_ok=True)
    start=time.monotonic(); used=0
    def fetch(name, **params):
        nonlocal used
        # Morning/evening share a directory but benchmark end_date changes.
        # Keep both vintages instead of returning morning's stale benchmark.
        key=name+'-'+str(params.get('end_date') if name=='index_daily' else params.get('trade_date','all'))
        path=root/(key+'.json')
        if path.exists(): return json.loads(path.read_text())['rows']
        if used>=max_requests or time.monotonic()-start>=max_seconds:
            raise RuntimeError('collection budget exhausted; resume same directory')
        used+=1
        rows=api(name,**params)
        atomic_json(path,{'api':name,'params':params,'fetched_at':datetime.now(timezone.utc).isoformat(),'rows':rows})
        print(key,len(rows),flush=True)
        return rows
    now=datetime.now(timezone.utc)
    from zoneinfo import ZoneInfo
    local=now.astimezone(ZoneInfo('Asia/Shanghai'))
    cal=fetch('trade_cal',exchange='SSE',start_date=(local.date()-timedelta(days=230)).strftime('%Y%m%d'),end_date=local.strftime('%Y%m%d'))
    sessions=sorted(r['cal_date'] for r in cal if r['is_open']=='1' and (r['cal_date']<local.strftime('%Y%m%d') or local.hour>=15))[-120:]
    if len(sessions)!=120: raise ValueError('calendar warmup incomplete')
    atomic_json(root/'sessions.json',sessions)
    fetch('index_daily',ts_code='000300.SH',start_date=sessions[0],end_date=sessions[-1])
    for d in sessions:
        for name in ('daily','adj_factor'):
            fetch(name,trade_date=d)
    atomic_json(root/'complete.json',{'asof':sessions[-1],'sessions':120,'new_requests':used,'completed_at':datetime.now(timezone.utc).isoformat()})

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',required=True);p.add_argument('--max-requests',type=int,default=250);p.add_argument('--max-seconds',type=int,default=3600)
    a=p.parse_args();collect(a.out,a.max_requests,a.max_seconds)
