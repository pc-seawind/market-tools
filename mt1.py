#!/usr/bin/env python3
"""MT-1.0 single-command data/report and explicit evidence event entry points."""
import argparse
import json
from datetime import datetime, timezone, date
from pathlib import Path
from mt1.data import HERE, atomic_json, cn_calendar, foreign_calendar
from mt1.calendar import gate
from mt1.pipeline import run, migrate
from mt1.store import Store
from mt1.plans import reduce_plan, eligible
from mt1.methods import reduce_method, register_existing
from mt1.backtest import replay
from mt1.finalize import finalize


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir',default=str(HERE/'.cron_state/mt1'))
    sub=parser.add_subparsers(dest='cmd',required=True)
    p=sub.add_parser('run')
    p.add_argument('phase',choices=['morning','evening','saturday','sunday'])
    p.add_argument('--investment-dir',default='/home/emox/work/investment')
    p.add_argument('--fixture'); p.add_argument('--now'); p.add_argument('--run-id')
    p.add_argument('--collect',action='store_true'); p.add_argument('--max-stocks',type=int,default=0)
    for cmd in ('plan-event','method-event'):
        p=sub.add_parser(cmd); p.add_argument('--input',required=True)
    p=sub.add_parser('backtest'); p.add_argument('--input',required=True); p.add_argument('--out',required=True)
    p=sub.add_parser('watchlist'); p.add_argument('--execute',action='store_true')
    p=sub.add_parser('finalize'); p.add_argument('--input',required=True); p.add_argument('--investment-dir',default='/home/emox/work/investment')
    p=sub.add_parser('register-existing-method'); p.add_argument('--reviewer',required=True)
    p=sub.add_parser('verify'); p.add_argument('--asof')
    p=sub.add_parser('sweep')
    p.add_argument('--asof',required=True)
    p.add_argument('--batch-size',type=int,default=100)
    p.add_argument('--workers',type=int,default=4)
    p.add_argument('--attempts',type=int,default=2)
    p.add_argument('--calls-per-second',type=float,default=3)
    p.add_argument('--max-seconds',type=int,default=6600)
    p.add_argument('--max-batches',type=int,default=0)
    p=sub.add_parser('coldstart-export'); p.add_argument('--out',required=True); p.add_argument('--batch-size',type=int,default=10)
    p=sub.add_parser('evidence'); p.add_argument('--kind',choices=['dispatch','delivery','inventory','collect-dispatch'],default='inventory'); p.add_argument('--run-id'); p.add_argument('--input')
    p=sub.add_parser('data-backfill');p.add_argument('--input',required=True);p.add_argument('--max-requests',type=int,default=100)
    p=sub.add_parser('data-audit')
    p=sub.add_parser('parallel-cycle');p.add_argument('phase',choices=['morning','evening','saturday','sunday']);p.add_argument('--panel');p.add_argument('--reviews')
    p=sub.add_parser('parallel-collect'); p.add_argument('--out',required=True)
    p=sub.add_parser('parallel-current'); p.add_argument('--panel',required=True); p.add_argument('--out',required=True); p.add_argument('--reviews')
    p=sub.add_parser('parallel'); p.add_argument('--panel',required=True); p.add_argument('--out',required=True); p.add_argument('--reviews')
    p=sub.add_parser('plans')
    args=parser.parse_args()
    if args.cmd=='parallel-cycle':
        from mt1.parallel_cycle import cycle
        print(json.dumps(cycle(args.state_dir,args.phase,args.panel,args.reviews),ensure_ascii=False));return
    if args.cmd=='parallel-collect':
        from mt1.parallel_collect import collect
        collect(args.out); return
    if args.cmd in ('parallel','parallel-current'):
        if args.cmd=='parallel-current':
            from mt1.parallel_collect import collect
            collect(args.panel)
        from mt1.parallel import run_parallel
        print(json.dumps(run_parallel(args.state_dir,args.panel,args.out,args.reviews),ensure_ascii=False)); return
    if args.cmd=='data-backfill':
        from mt1.data_readiness import backfill
        r=backfill(args.state_dir,json.loads(Path(args.input).read_text()),args.max_requests)
        print(json.dumps(r,ensure_ascii=False,indent=2));return
    if args.cmd=='data-audit':
        from mt1.data_readiness import audit
        print(json.dumps(audit(args.state_dir),ensure_ascii=False,indent=2));return
    if args.cmd=='coldstart-export':
        from mt1.coldstart import export
        print(json.dumps(export(args.state_dir,args.out,args.batch_size),ensure_ascii=False,indent=2));return
    if args.cmd=='evidence':
        from mt1.evidence import attach,inventory,collect_dispatch
        r=(inventory(args.state_dir) if args.kind=='inventory' else collect_dispatch(args.state_dir,args.run_id) if args.kind=='collect-dispatch' else attach(args.state_dir,args.run_id,args.kind,args.input))
        print(json.dumps(r,ensure_ascii=False,indent=2));return
    if args.cmd=='sweep':
        from mt1.sweep import sweep
        r=sweep(args.state_dir,args.asof,args.batch_size,args.workers,args.attempts,args.calls_per_second,args.max_seconds,args.max_batches)
        print(json.dumps(r,ensure_ascii=False,indent=2))
        if not r['complete']:raise SystemExit(75)
        return
    if args.cmd=='verify':
        from mt1.verify import verify
        print(json.dumps(verify(args.state_dir,date.fromisoformat(args.asof) if args.asof else None),ensure_ascii=False,indent=2)); return
    if args.cmd=='run':
        result=run(args.phase,args.state_dir,args.investment_dir,
                   datetime.fromisoformat(args.now) if args.now else None,
                   json.loads(Path(args.fixture).read_text()) if args.fixture else None,
                   args.collect,args.max_stocks,args.run_id)
        print(json.dumps({'run_id':result['run_id'],'report_path':result['report_path'],
              'errors':result['errors'],'gates':result['gates']},ensure_ascii=False,indent=2))
        print('RESULT_JSON='+str(Path(args.state_dir)/'runs'/result['run_id']/'report.json'))
        return
    if args.cmd=='finalize':
        print(json.dumps(finalize(json.loads(Path(args.input).read_text()),args.state_dir,args.investment_dir),ensure_ascii=False,indent=2)); return
    if args.cmd=='backtest':
        r=replay(json.loads(Path(args.input).read_text())); atomic_json(args.out,r)
        print(json.dumps(r,ensure_ascii=False,indent=2)); return
    store=Store(Path(args.state_dir)/'plans.db')
    try:
        if args.cmd=='register-existing-method':
            r=register_existing(store,HERE,args.reviewer)
        elif args.cmd in ('plan-event','method-event'):
            d=json.loads(Path(args.input).read_text()); kind='plan' if args.cmd=='plan-event' else 'method'
            reducer=(lambda old, patch: reduce_plan(old, patch, store.latest)) if kind=='plan' else reduce_method
            if d['payload'].get(kind+'_id', d['id']) != d['id']:
                raise ValueError('entity id mismatch')
            current=store.latest('plan:'+d['id']) or {} if kind=='plan' else {}
            proposed={**current,**d['payload']}
            if kind=='plan' and (proposed.get('state')=='BUY' or proposed.get('unheld_direction')=='BUY'):
                now=datetime.now(timezone.utc)
                current=store.latest('plan:'+d['id']) or {}
                market=d['payload'].get('market',current.get('market'))
                cal=cn_calendar(now) if market=='CN' else foreign_calendar(now,market)
                g=gate(cal,market,'morning',now)
                if not g['allowed']: raise ValueError('BUY blocked by market calendar: '+g['reason'])
            r=store.apply(kind+':'+d['id'],d['expected_version'],d['request_id'],d['payload'],d['reason'],reducer)
        elif args.cmd=='watchlist':
            from watchlist_sync import sync_buys
            if args.execute:
                now=datetime.now(timezone.utc)
                if not gate(cn_calendar(now),'CN','morning',now)['allowed']:
                    raise ValueError('watchlist execution blocked by CN calendar')
            buys=[{'code':p['code'],'name':p.get('name',''),'reason':f"MT-1.0 {p['plan_id']} v{p['version']}",
                   '_mt1_final_plan':p} for p in store.all() if p['market']=='CN' and eligible(p,date.today(),store.latest)]
            r=sync_buys(buys,group='默认组',cooldown_days=30,dry_run=not args.execute,source='MT-1.0-final', method_lookup=store.latest)
        else: r=store.all()
        print(json.dumps(r,ensure_ascii=False,indent=2))
    finally: store.close()


if __name__=='__main__': main()
