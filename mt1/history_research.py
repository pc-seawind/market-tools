"""Isolated MT12 current-vintage historical research; never a trading interface.

Usage: python -m mt1.history_research --out reports/mt12-history-20260912 [--pilot] [--offline]
The pre-existing frozen-contract.json is mandatory. Raw successful HTTP bodies
are immutable, hash-checked checkpoints. Failed responses remain separate.
"""
import argparse
import copy
import hashlib
import json
import math
import os
import platform
import subprocess
import time
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .timing import contract, digest, indicators, step
from .parallel import timing as old_timing
from .timing_experiment import aggregate, exit_signal

ROOT = Path(__file__).resolve().parents[1]


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)+'\n')
    tmp.replace(path)


def log(out, stage, **details):
    event = dict(at=now(), stage=stage, **details)
    with (out/'stages.jsonl').open('a') as f:
        f.write(json.dumps(event, ensure_ascii=False)+'\n')
    print(json.dumps(event, ensure_ascii=False), flush=True)


class Collector:
    def __init__(self, out, offline=False):
        self.out, self.offline = out, offline
        (out/'raw').mkdir(exist_ok=True)

    def get(self, api, **params):
        request = dict(api_name=api, params=params, fields='')
        key = api+'-'+digest(request)[:20]
        body, meta = self.out/'raw'/(key+'.json'), self.out/'raw'/(key+'.meta.json')
        if meta.exists():
            m = json.loads(meta.read_text())
            if sha(body) != m['sha256'] or m['request'] != request:
                raise ValueError('raw_checkpoint_changed')
        else:
            if self.offline:
                raise ValueError('offline_checkpoint_missing:'+key)
            token = os.environ.get('TUSHARE_TOKEN')
            if not token:
                raise ValueError('credential_missing:TUSHARE_TOKEN')
            log(self.out, 'fetch', api=api, params=params)
            # No request body or token is persisted/logged. Actual HTTP response
            # bytes (not parsed reserialization, not shared mutable cache).
            req = urllib.request.Request('https://api.tushare.pro',
                data=json.dumps({**request, 'token':token}).encode(),
                headers={'Content-Type':'application/json'})
            last = None
            for attempt in range(3):
                try:
                    with urllib.request.urlopen(req, timeout=30) as response:
                        raw = response.read()
                    parsed = json.loads(raw)
                    if parsed.get('code') != 0:
                        fail = self.out/'raw'/(key+f'.failure-{time.time_ns()}.json')
                        fail.write_bytes(raw)
                        raise ValueError(f"provider_error:{parsed.get('code')}:{parsed.get('msg')}")
                    body.write_bytes(raw)
                    write(meta, dict(request=request, endpoint='https://api.tushare.pro', fetched_at=now(),
                        sha256=sha(body), kind='actual_http_response_bytes', rows=len(parsed['data']['items'])))
                    break
                except Exception as e:
                    last = e
                    log(self.out,'fetch_failed', api=api, attempt=attempt, error=str(e))
                    if '权限' in str(e) or '没有' in str(e):
                        raise
                    time.sleep(2*(attempt+1))
            else:
                raise last
        parsed = json.loads(body.read_bytes())['data']
        return [dict(zip(parsed['fields'], row)) for row in parsed['items']], str(body.relative_to(self.out)), sha(body)


def yearly(c, api, spec, **params):
    result, sources = [], []
    for year in range(int(spec['start'][:4]), int(spec['end'][:4])+1):
        rows, path, h = c.get(api, start_date=max(spec['start'],f'{year}0101'),
                            end_date=min(spec['end'],f'{year}1231'), **params)
        if len(rows) >= 5000:
            raise ValueError('possible_truncated_response:'+api)
        result.extend(rows); sources.append(dict(path=path, sha256=h))
    return result, sources


def collect(out, spec, pilot=False, offline=False):
    c = Collector(out, offline)
    calendars = {}
    sources = []
    for exchange in ('SSE','SZSE'):
        raw, refs = yearly(c,'trade_cal',spec,exchange=exchange)
        calendars[exchange] = sorted(r['cal_date'] for r in raw if int(r['is_open'])==1)
        sources += refs
    if not calendars['SSE'] or calendars['SSE'] != calendars['SZSE']:
        raise ValueError('exchange_calendars_disagree_requires_market_specific_folds')
    dates = calendars['SSE']
    benchmark, refs = yearly(c,'index_daily',spec,ts_code='000300.SH')
    sources += refs
    broad = {r['trade_date']:float(r['close']) for r in benchmark}
    if sorted(broad) != dates:
        raise ValueError('benchmark_calendar_coverage_mismatch')
    panels, failures = [], []
    codes = spec['codes'][:spec['pilot_count']] if pilot else spec['codes']
    for code in codes:
        try:
            data, refs = yearly(c,'daily',spec,ts_code=code)
            factors, fr = yearly(c,'adj_factor',spec,ts_code=code)
            limits, lr = yearly(c,'stk_limit',spec,ts_code=code)
            susp, sr = yearly(c,'suspend_d',spec,ts_code=code)
            basic, bp, bh = c.get('stock_basic', ts_code=code, list_status='L')
            if len(basic)!=1:
                raise ValueError('current_stock_basic_not_unique')
            if not basic[0].get('list_date') or basic[0]['list_date']>spec['start']:
                raise ValueError('not_listed_before_frozen_start')
            if any(r.get('ts_code')!=code for r in data+factors+limits+susp):
                raise ValueError('foreign_symbol_response')
            refs += fr+lr+sr+[dict(path=bp,sha256=bh)]
            lookup = {}
            for rows, key in ((data,'daily'),(factors,'factor'),(limits,'limit')):
                d = {r['trade_date']:r for r in rows}
                if len(d)!=len(rows) or set(d)-set(dates):
                    raise ValueError('duplicate_or_foreign_calendar:'+key)
                lookup[key] = d
            susp_by_date = defaultdict(list)
            for r in susp:
                susp_by_date[r['trade_date']].append(r)
            acquired_at=max(json.loads((out/r['path'].replace('.json','.meta.json')).read_text())['fetched_at'] for r in refs)
            bars, evidence = [], []
            for i, date in enumerate(dates):
                d, f, lim = (lookup[k].get(date) for k in ('daily','factor','limit'))
                ev = dict(date=date, open_at=f'{date[:4]}-{date[4:6]}-{date[6:]}T09:30:00+08:00',
                    realization_at=f'{date[:4]}-{date[4:6]}-{date[6:]}T15:00:00+08:00',
                    acquired_at=acquired_at, suspension=susp_by_date.get(date,[]),
                    daily_present=bool(d), factor_present=bool(f), limit=lim,
                    current_listing=basic[0], availability='ex_post_vendor_vintage_NOT_PIT_known_at')
                evidence.append(ev)
                if d and f:
                    factor = float(f['adj_factor'])
                    if not math.isfinite(factor) or factor<=0 or not math.isfinite(float(d['vol'])) or float(d['vol'])<=0:
                        bars.append(None); continue
                    b = dict(date=date, close_at=ev['realization_at'], open_at=ev['open_at'], index=i,
                        factor=factor, vol=float(d['vol']), raw={k:float(d[k]) for k in ('open','high','low','close')}, evidence=ev)
                    b.update({k:v*factor for k,v in b['raw'].items()})
                    if not all(math.isfinite(b[k]) for k in ('open','high','low','close')):
                        raise ValueError('nonfinite_price')
                    if not 0<b['low']<=min(b['open'],b['close'])<=max(b['open'],b['close'])<=b['high']:
                        raise ValueError('invalid_ohlc')
                    bars.append(b)
                else:
                    bars.append(None)
            panel = dict(code=code, market='CN', channel='TREND', industry='unknown',
                         industry_current_vintage=basic[0].get('industry'), dates=dates, bars=bars,
                         evidence=evidence, sources=refs, benchmark=broad)
            panels.append(panel)
            write(out/f'panel-{code}.json', panel)
            log(out,'panel_complete',code=code,sessions=len(dates),bars=sum(b is not None for b in bars))
        except Exception as e:
            failures.append(dict(code=code,error=str(e)))
            log(out,'panel_failed',code=code,error=str(e))
    return dict(panels=panels, failures=failures, sources=sources, dates=dates)


def next_open(panel, signal, end, side, scenario, entry=None):
    """Separate historical model. Never stamps ex-post data as forward known_at.

    Daily volume does not prove auction liquidity: both are explicit scenarios,
    no queue/size certainty. Conservative uses daily extrema only as an ex-post
    fill stress, never to generate signals. Unknown stops, never skips to gain.
    """
    skipped = []
    for i in range(signal+1, min(end,len(panel['bars']))):
        if entry is not None and i-entry<1:
            continue
        b, e = panel['bars'][i], panel['evidence'][i]
        susp = [s for s in e['suspension'] if s['suspend_type']=='S']
        if susp:
            skipped.append(dict(index=i,date=e['date'],reason='suspension_whole_or_intraday_conservative'))
            continue
        if b is None or e.get('limit') is None:
            return dict(status='unknown',reason='missing_bar_factor_or_limit',date=e['date'],skipped=skipped)
        lim = e['limit']
        if not lim.get('up_limit') or not lim.get('down_limit'):
            return dict(status='unknown',reason='limit_not_identified',date=e['date'],skipped=skipped)
        upper, lower = float(lim['up_limit']), float(lim['down_limit'])
        raw = b['raw']
        if not 0<lower<upper or not lower-0.011<=raw['open']<=upper+0.011:
            return dict(status='unknown',reason='limit_price_inconsistent',date=e['date'],skipped=skipped)
        constrained = raw['open']>=upper-0.005 if side=='buy' else raw['open']<=lower+0.005
        if scenario=='any_limit_touch_conservative_v1':
            constrained |= raw['high']>=upper-0.005 if side=='buy' else raw['low']<=lower+0.005
        if constrained:
            skipped.append(dict(index=i,date=e['date'],reason=side+'_limit_constraint'))
            continue
        return dict(status='filled',index=i,date=b['date'],price=b['open'],raw_price=raw['open'],factor=b['factor'],
                    side=side,model=scenario,open_at=b['open_at'],realization_at=e['realization_at'],
                    evidence_index=i,skipped=skipped,settlement='CN_Tplus1_exchange_sessions',
                    gap_from_signal_close=b['open']/panel['bars'][signal]['close']-1,
                    source_hash=digest(panel['sources']))
    return dict(status='pending',reason='no_verified_open_before_fold_end',skipped=skipped)


def history_prefix(bars, index):
    # Restart warmup after holes; never compress away missing exchange sessions.
    start = index
    while start>=0 and bars[start] is not None:
        start-=1
    return bars[start+1:index+1]


def replay_trade(panel, signal, end, rule, cfg, scenario, breakout=None):
    rows=panel['bars']
    r=dict(signal_date=rows[signal]['date'], signal_index=signal, exit_rule=rule, status='pending',
           net_price_return=None,max_drawdown=None,post_exit_rally=None,false_breakout=None,round_trips=0,turnover=0)
    ent=next_open(panel,signal,end,'buy',scenario)
    r['entry_fill']=ent
    if ent['status']!='filled':
        return {**r,'status':ent['status']}
    start=ent['index']
    state,_=step(history_prefix(rows,signal),None,cfg,channel='TREND',observed_at=rows[signal]['close_at'])
    if breakout:
        state['breakout_episode']={**breakout,'session':state['observed_sessions'],'failed_closes':0}
    ex=None
    for i in range(signal+1,min(end,len(rows))):
        if rows[i] is None:
            # A verified suspended day cannot provide a price signal; state is
            # retained, but no synthetic bar is produced. Unknown hole blocks.
            e=panel['evidence'][i]
            if any(s['suspend_type']=='S' for s in e['suspension']):
                continue
            return {**r,'status':'unknown','reason':'monitoring_price_gap','turnover':int(i>=start)}
        prefix=history_prefix(rows,i)
        if len(prefix)<cfg['warmup']:
            return {**r,'status':'unknown','reason':'warmup_after_price_gap','turnover':int(i>=start)}
        state,card=step(prefix,state,cfg,channel='TREND',observed_at=rows[i]['close_at'])
        risk=exit_signal(card,rule)
        if i<start:
            if risk:
                return {**r,'status':'cancelled_before_entry','entry_fill':dict(status='cancelled',reason='risk_before_delayed_entry')}
            continue
        if risk or i-start>=60:
            ex=next_open(panel,i,end,'sell',scenario,start)
            r.update(exit_fill=ex,exit_signal_date=rows[i]['date'],exit_reason=card['risk']['reasons'] if rule!='old_ma60_5' else ['old_ma60_or_horizon'])
            break
    r['turnover']=1
    if not ex or ex['status']!='filled':
        mark=rows[min(end,len(rows))-1]
        if mark is not None:
            cost=cfg['execution']['per_side_cost_bps']['CN']/10000
            values=[1]+[b['close']/(ent['price']*(1+cost)) for b in rows[start:min(end,len(rows))] if b]
            peak,dd=1,0
            for v in values:
                peak=max(peak,v);dd=min(dd,v/peak-1)
            r.update(unrealized_price_mark=values[-1]-1,unrealized_close_drawdown=dd,mark_date=mark['date'],
                     mark_basis='not_liquidated_entry_cost_only_not_realized_return')
        return {**r,'status':ex['status'] if ex else 'not_matured'}
    stop=ex['index']; cost=cfg['execution']['per_side_cost_bps']['CN']/10000
    net=ex['price']*(1-cost)/(ent['price']*(1+cost))-1
    wealth=[1]+[b['close']/(ent['price']*(1+cost)) for b in rows[start:stop] if b]+[1+net]
    peak,dd=1,0
    for v in wealth:
        peak=max(peak,v);dd=min(dd,v/peak-1)
    rally=None
    # Diagnostic outcome is kept inside common OOS, not future test/purge data.
    if stop+20<end and all(rows[stop+1:stop+21]):
        rally=max(b['close'] for b in rows[stop+1:stop+21])/ex['price']-1
    failed=None
    if breakout and signal+10<end and all(rows[signal+1:signal+11]):
        count=0;failed=False
        for b in rows[signal+1:signal+11]:
            count=count+1 if b['close']<breakout['level'] else 0
            failed|=count>=2
    return {**r,'status':'closed','net_price_return':net,'max_drawdown':dd,'post_exit_rally':rally,
            'false_breakout':failed,'round_trips':1,'turnover':2,'holding_sessions':stop-start}


def signals(panel,cfg):
    state=None; result={}; coverage=Counter()
    for i,b in enumerate(panel['bars']):
        if b is None:
            state=None;coverage['missing_bar']+=1;continue
        prefix=history_prefix(panel['bars'],i)
        if len(prefix)<cfg['warmup']:
            coverage['warmup']+=1;continue
        state,card=step(prefix,state,cfg,channel='TREND',observed_at=b['close_at'])
        f=card['indicators']; broad=panel['benchmark']; prev=prefix[-61]
        rs=f['close']/prev['close']-broad[b['date']]/broad[prev['date']]
        old=old_timing({**f,'rs60':rs},f['asof'],f['asof'])['status']
        result[i]=dict(statuses={'old_trigger':old,**{k:v['status'] for k,v in card['entry'].items()}},
                       breakout=copy.deepcopy(state.get('breakout_episode')),indicators=f)
        coverage['evaluated']+=1
    return result,dict(coverage)


def analyze(bundle,spec,out,label):
    cfg=spec['trial_contract']; trades=[]; folds=[]; audits=[]; episodes=[]
    for p in bundle['panels']:
        sigs,cov=signals(p,cfg)
        last={}; last_a=-1000
        for fold,start in enumerate(range(180,len(p['dates']),120)):
            end=min(start+60,len(p['dates']))
            folds.append(dict(code=p['code'],fold=fold,train_start=p['dates'][start-180],train_end=p['dates'][start-61],
                purge_start=p['dates'][start-60],purge_end=p['dates'][start-1],test_start=p['dates'][start],test_end=p['dates'][end-1],
                test_sessions=end-start,refit=False))
            selected={}; a=None; opportunities=Counter()
            for i in range(start,end):
                if i not in sigs:
                    opportunities['unknown_or_warmup']+=1;continue
                s=sigs[i]
                for arm,status in s['statuses'].items():
                    opportunities[arm+'|'+status]+=1
                    if status=='trigger' and arm not in selected and i-last.get(arm,-1000)>60:
                        selected[arm]=i;last[arm]=i
                if a is None and s['statuses']['old_trigger']=='trigger' and i-last_a>60:
                    a=i;last_a=i
            audits.append(dict(code=p['code'],fold=fold,coverage=cov,opportunities=dict(opportunities)))
            specs=[]
            if a is not None:
                eid=f"{p['code']}:fold{fold}:{p['dates'][a]}"
                ep=dict(id=eid,code=p['code'],fold=fold,signal_date=p['dates'][a],basis='historical_old_trigger_not_recommendation',
                    source_hash=digest(p['sources']),signal_hash=digest(sigs[a]),fold_end=p['dates'][end-1])
                episodes.append(ep)
                specs.extend(('A',arm,a,eid,None) for arm in cfg['group_A']['exits'])
            specs.extend(('B',arm,i,None,sigs[i]['breakout'] if arm=='breakout' else None) for arm,i in selected.items())
            for scenario in spec['execution_scenarios']:
                for group,arm,i,eid,breakout in specs:
                    rule=arm if group=='A' else cfg['group_B']['exit']
                    t=replay_trade(p,i,end,rule,cfg,scenario,breakout)
                    f=sigs[i]['indicators']
                    # Price paths/exits invariant to fees. Revalue only net and
                    # fee-inclusive close drawdown for every prespecified cost.
                    for bps in spec['costs_bps_per_side']:
                        u=copy.deepcopy(t)
                        if t['entry_fill']['status']=='filled':
                            cc=copy.deepcopy(cfg);cc['execution']['per_side_cost_bps']['CN']=bps
                            u=replay_trade(p,i,end,rule,cc,scenario,breakout)
                        trades.append({**u,'group':group,'arm':arm,'scenario':scenario,'cost_bps':bps,
                            'code':p['code'],'fold':fold,'episode_id':eid,'market':'CN','industry':'unknown',
                            'industry_current_vintage':p['industry_current_vintage'],
                            'volatility':'high' if f['atr20']/f['close']>.04 else 'low' if f['atr20']/f['close']<.02 else 'medium',
                            'regime':'uptrend' if f['close']>f['ma60']>f['ma60_5ago'] else 'other'})
        log(out,'replayed',code=p['code'],trades=len(trades))
    grouped=defaultdict(list); strata=defaultdict(list)
    for t in trades:
        key=f"{t['scenario']}|{t['cost_bps']}|{t['group']}|{t['arm']}"
        grouped[key].append(t)
        for field in ('market','industry','volatility','regime','industry_current_vintage','code','fold'):
            strata[key+'|'+field+'|'+str(t[field])].append(t)
    for scenario in spec['execution_scenarios']:
        for bps in spec['costs_bps_per_side']:
            for group in ('A','B'):
                for arm in cfg['group_A']['exits'] if group=='A' else cfg['group_B']['entries']:
                    grouped[f'{scenario}|{bps}|{group}|{arm}']
    result=dict(mode='current_vintage_adjusted_price_timing_diagnostic_NOT_PIT_selection_NOT_portfolio',
        label=label,contract_sha256=sha(out/'frozen-contract.json'),trades=trades,episodes=episodes,folds=folds,audit=audits,
        collection_failures=bundle['failures'],summary={k:aggregate(v) for k,v in grouped.items()},strata={k:aggregate(v) for k,v in strata.items()})
    write(out/f'{label}-results.json',result)
    return result


def validate_spec(spec):
    codes=spec['codes']
    if len(codes)!=len(set(codes)) or set(codes)&set(spec['excluded']['user_holdings']):
        raise ValueError('research_scope_duplicate_or_user_holding')
    if spec['start']>=spec['end'] or int(spec['end'][:4])-int(spec['start'][:4])<3:
        raise ValueError('historical_span_too_short')
    if spec['trial_contract']!=contract():
        raise ValueError('frozen_trial_contract_changed')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True);parser.add_argument('--pilot',action='store_true');parser.add_argument('--offline',action='store_true')
    parser.add_argument('--verify',action='store_true',help='independently verify archived real trades, raw hashes and old evidence')
    args=parser.parse_args();out=args.out.resolve()
    if args.verify:
        from .history_research_verify import verify
        print(json.dumps(verify(out),ensure_ascii=False,indent=2));return
    spec=json.loads((out/'frozen-contract.json').read_text())
    validate_spec(spec)
    if (out/'stages.jsonl').exists():
        starts=[json.loads(line) for line in (out/'stages.jsonl').read_text().splitlines() if json.loads(line)['stage']=='start']
        if starts and starts[0]['contract_sha256']!=sha(out/'frozen-contract.json'):
            raise ValueError('frozen_scope_or_parameters_changed')
    if spec['trial_contract']!=contract():
        raise ValueError('frozen_trial_contract_changed')
    label='pilot' if args.pilot else 'full'
    log(out,'start',label=label,offline=args.offline,contract_sha256=sha(out/'frozen-contract.json'))
    bundle=collect(out,spec,args.pilot,args.offline)
    write(out/f'{label}-collection.json',bundle)
    result=analyze(bundle,spec,out,label)
    from .history_research_report import report
    report(out,bundle,result,spec,label)
    write(out/f'{label}-run.json',dict(completed_at=now(),python=platform.python_version(),
        command=f'python -m mt1.history_research --out {out} '+('--pilot ' if args.pilot else '')+'--offline',
        git_head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        implementations={n:sha(ROOT/'mt1'/n) for n in ('history_research.py','history_research_report.py','timing.py','timing_experiment.py','parallel.py')},
        results_sha256=sha(out/f'{label}-results.json')))
    log(out,'complete' if not bundle['failures'] else 'partial_failure',label=label,closed=sum(t['status']=='closed' for t in result['trades']))
    if bundle['failures']:
        raise SystemExit(2)


if __name__=='__main__':
    main()
