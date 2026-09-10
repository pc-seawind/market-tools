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
from mt1.methods import reduce_method
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
    p=sub.add_parser('plans')
    args=parser.parse_args()
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
        if args.cmd in ('plan-event','method-event'):
            d=json.loads(Path(args.input).read_text()); kind='plan' if args.cmd=='plan-event' else 'method'
            reducer=reduce_plan if kind=='plan' else reduce_method
            if d['payload'].get(kind+'_id', d['id']) != d['id']:
                raise ValueError('entity id mismatch')
            if kind=='plan' and d['payload'].get('state')=='BUY':
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
                   '_mt1_final_plan':p} for p in store.all() if p['market']=='CN' and eligible(p,date.today())]
            r=sync_buys(buys,group='默认组',cooldown_days=30,dry_run=not args.execute,source='MT-1.0-final')
        else: r=store.all()
        print(json.dumps(r,ensure_ascii=False,indent=2))
    finally: store.close()


if __name__=='__main__': main()
