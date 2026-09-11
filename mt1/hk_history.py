"""Isolated HK historical model. Daily-open estimator, not auction-fill proof.
No CN limits, no T+1 resale lock, no production state or trading interfaces.
All three frozen names and all folds retained regardless of results.
"""
import argparse
import ast
import bisect
import copy
import hashlib
import json
import math
import re
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from statistics import mean
from .history_research import sha, write, log, signals, history_prefix
from .timing import contract, digest, step
from .timing_experiment import exit_signal, aggregate
from .history_cutoff import value_event, summarize, compare_pairs

ROOT = Path(__file__).resolve().parents[1]
R1 = ROOT/'reports/mt12-history-20260912'
R2 = ROOT/'reports/mt12-history-20260912-r2'
CODES = ('00005','00388','00941')
FIELDS = ('open','high','low','close')


def read_checked(path):
    path=Path(path)
    meta=path.with_name(path.stem+'.meta.json')
    if meta.exists() and json.loads(meta.read_text())['sha256'] != sha(path):
        raise ValueError('raw_hash_changed:'+str(path))
    return path.read_bytes()


def valid_price(b):
    if not b or not all(isinstance(b.get(k),(float,int)) and math.isfinite(b[k]) for k in (*FIELDS,'volume')):
        return False
    return b['volume']>0 and 0<b['low']<=min(b['open'],b['close'])<=max(b['open'],b['close'])<=b['high']


def matches(a,b):
    return valid_price(a) and valid_price(b) and all(abs(a[k]-b[k])<=.011 for k in FIELDS)


def choose_price(yahoo,tencent,third=None):
    if matches(yahoo,tencent):return tencent,'two_vendor_agreement'
    if matches(third,tencent):return tencent,'third_source_supports_Tencent'
    if matches(third,yahoo):return yahoo,'third_source_supports_Yahoo'
    return None,'unknown_unresolved_price'


def news_coverage(raw,code):
    """Empty halt query alone cannot open the gate. Check nonempty corpus,
    identity, dates, count, pagination, and duplicate IDs for each whole year.
    Mandatory disclosure + complete corpus yields a reconstruction, not OMD ticks.
    Unknown/malformed/positive halt records close the gate until interval decoded.
    """
    refs=[];count=0;halting=[]
    for year in range(2020,2026):
        path=raw/f'corpus_{code}_{year}.raw';d=json.loads(read_checked(path));rows=json.loads(d['result'])
        if not rows or d['hasNextRow'] or len(rows)!=int(d['recordCnt']) or int(d['loadedRecord'])!=len(rows):
            raise ValueError('incomplete_news_corpus')
        if len({r['NEWS_ID'] for r in rows})!=len(rows):raise ValueError('duplicate_news')
        for r in rows:
            if code not in r['STOCK_CODE'].split('<br/>') or datetime.strptime(r['DATE_TIME'],'%d/%m/%Y %H:%M').year!=year:
                raise ValueError('foreign_news_stock_or_date')
            if any(x in r['LONG_TEXT'].lower() for x in ('trading halt','suspension','resumption')):
                halting.append(r)
        count+=len(rows);refs.append(dict(path=str(path.relative_to(ROOT)),sha256=sha(path),records=len(rows)))
    for cat in ('17650','17850','17960'):
        path=raw/f'halts_{code}_{cat}.raw';d=json.loads(read_checked(path));rows=json.loads(d['result'])
        if d['hasNextRow'] or len(rows)!=int(d['recordCnt']):raise ValueError('incomplete_halt_query')
        halting+=rows;refs.append(dict(path=str(path.relative_to(ROOT)),sha256=sha(path),records=len(rows)))
    return dict(status='unknown_unparsed_halt_intervals' if halting else 'disclosure_reconstructed_no_halt',
        records=count,halt_records=halting,sources=refs,
        inference='mandatory publication rules + complete nonempty stock/year corpus + three explicit halt category cross-checks; not zero search alone; not exchange tick-state certification')


def calendar(out):
    """Annual official holiday tables independently reconciled to vendor calendar.
    Half days and weather opens are ex-post actual session labels, not PIT known_at.
    """
    raw=out/'raw';holidays=set();half=set();refs=[]
    pat=r'^\s*(\d{1,2} [A-Za-z]+ 202[1-5])\s+(?:Monday|Tuesday|Wednesday|Thursday|Friday)\b'
    for y in range(2021,2026):
        p=raw/f'holiday_{y}.raw';read_checked(p)
        txt=subprocess.check_output(['pdftotext','-layout',str(p),'-'],text=True)
        (out/f'holiday-{y}.txt').write_text(txt)
        before=txt.split('The following trading day')[0]
        holidays.update(datetime.strptime(x,'%d %B %Y').strftime('%Y%m%d') for x in re.findall(pat,before,re.M))
        after=txt.split('The following trading day')[-1] if 'The following trading day' in txt else ''
        half.update(datetime.strptime(x,'%d %B %Y').strftime('%Y%m%d') for x in re.findall(r'(\d{1,2} [A-Za-z]+ 202[1-5])\s*\(',after))
        refs.append(dict(path=str(p.relative_to(ROOT)),sha256=sha(p)))
    closed={
        '20211013':raw/'weather_2110132.raw',
        '20230717':R2/'hk/official_market_closed_20230717.raw',
        '20230901':R2/'hk/official_market_closed_20230901.raw',
        '20230908':R2/'hk/official_market_closed_20230908.raw',
        '20240906':raw/'weather_2409062.raw'}
    # Paths in r2 have an alternative descriptive suffix; resolve only exact date.
    for d,p in list(closed.items()):
        if not p.exists():
            candidates=list((R2/'hk').glob('*'+d+'*.raw'))
            if len(candidates)!=1:raise ValueError('missing_official_closed_day:'+d)
            closed[d]=candidates[0]
    for d,p in closed.items():
        read_checked(p);refs.append(dict(path=str(p.relative_to(ROOT)),sha256=sha(p)))
    dates=[];d=datetime(2021,1,1)
    while d.year<2026:
        s=d.strftime('%Y%m%d')
        if d.weekday()<5 and s not in holidays and s not in closed:dates.append(s)
        d+=timedelta(days=1)
    r=json.loads((R2/'hk/authorized-existing-raw-expansion.json').read_text())
    ref=next(x for x in r if x['api']=='hk_tradecal' and x['status']=='ok');p=R2/'hk'/ref['path']
    if sha(p)!=ref['sha256']:raise ValueError('calendar_raw_hash')
    data=json.loads(p.read_text())['data'];vs=sorted(x['cal_date'] for x in (dict(zip(data['fields'],r)) for r in data['items']) if int(x['is_open']))
    if dates!=vs:raise ValueError('official_vendor_calendar_difference:'+str(sorted(set(dates)^set(vs))))
    delayed={'20210628':('13:30','weather_2106283'),'20220825':('13:00','weather_2208252'),'20231009':('14:00','weather_2310092')}
    table=[]
    for date in dates:
        op='09:20';cl='12:10' if date in half else '16:10';source=[]
        if date in delayed:
            op,n=delayed[date];p=raw/(n+'.raw');read_checked(p);source.append(dict(path=str(p.relative_to(ROOT)),sha256=sha(p)))
        if date=='20221102':
            cl='13:55';p=raw/'weather_221102.raw';read_checked(p);source.append(dict(path=str(p.relative_to(ROOT)),sha256=sha(p)))
        table.append(dict(date=date,open_at=f'{date[:4]}-{date[4:6]}-{date[6:]}T{op}:00+08:00',
            close_at=f'{date[:4]}-{date[4:6]}-{date[6:]}T{cl}:00+08:00',
            open_window='09:20-09:30' if date not in delayed else op,
            timestamp_basis='official_session_window_NOT_first_trade_timestamp',half_day=date in half,
            delayed_open=date in delayed,market_exception_sources=source,
            weather_rule='SWT_20240923' if date>='20240923' else 'pre_SWT_actual_exceptions',
            availability='historical_actual_schedule_collected_ex_post_not_PIT_announcement_time'))
    write(out/'market-calendar.json',dict(sessions=table,holiday_dates=sorted(holidays),half_days=sorted(half),
        closed_exceptions={k:str(v.relative_to(ROOT)) for k,v in closed.items()},sources=refs,independent_calendar_matches=True))
    return table,refs


def assemble(out):
    raw=out/'raw';cal,calendar_refs=calendar(out);dates=[e['date'] for e in cal]
    from .hk_free_probe import chart
    charts={n:chart(read_checked(R2/'hk'/f'yahoo_{n}.raw')) for n in ('hsbc','hkex','mobile','hsi')}
    benchmark={b['date']:b['close'] for b in charts['hsi']['rows']}
    if sorted(benchmark)!=dates:raise ValueError('HSI_calendar_mismatch')
    from bs4 import BeautifulSoup
    vp=R2/'hk/hkex_vcm_history.raw';soup=BeautifulSoup(read_checked(vp),'html.parser');vcm=defaultdict(list);vcm_all=0
    for tr in soup.find_all('tr'):
        text=tr.get_text(' ',strip=True);m=re.match(r'(\d{2} [A-Za-z]{3} 20\d{2})\s+(\d{5})\s+',text)
        if m:
            vcm_all+=1;date=datetime.strptime(m[1],'%d %b %Y').strftime('%Y%m%d');vcm[(m[2],date)].append(text)
    if vcm_all<500:raise ValueError('VCM_archive_incomplete')
    panels=[];quality=[];elig=[];news={}
    for code,name in zip(CODES,('hsbc','hkex','mobile')):
        news[code]=news_coverage(raw,code);yahoo={b['date']:b for b in charts[name]['rows']};vendor={};refs=calendar_refs+news[code]['sources']
        for year in range(2021,2026):
            p=R2/'hk'/f'tencent_qfq_request_{code}_{year}.raw';data=json.loads(read_checked(p))['data']['hk'+code]
            for row in data['day']:
                date=row[0].replace('-','');vendor[date]=dict(date=date,open=float(row[1]),close=float(row[2]),high=float(row[3]),low=float(row[4]),volume=float(row[5]))
            refs.append(dict(path=str(p.relative_to(ROOT)),sha256=sha(p)))
        # The decoded Sina response is reproduced during --decode, without a network request.
        p=out/f'sina_daily_{code}.json';sina={b['date'][:10].replace('-',''):b for b in json.loads(p.read_text())}
        fp=R2/'hk'/f'sina_{name}_qfq.raw';factorrows=ast.literal_eval(read_checked(fp).decode().split('=',1)[1].split('\n',1)[0].rstrip(';'))['data']
        ff=sorted((x['d'].replace('-',''),float(x['f'])) for x in factorrows);fd=[x[0] for x in ff]
        anchor=ff[bisect.bisect_right(fd,'20251231')-1][1]
        for p in [fp,raw/f'sina_daily_{code}.raw',vp,R2/'hk'/f'yahoo_{name}.raw']:
            refs.append(dict(path=str(p.relative_to(ROOT)),sha256=sha(p)))
        bars=[];evidence=[]
        for i,ce in enumerate(cal):
            date=ce['date'];b,reason=choose_price(yahoo.get(date),vendor.get(date),sina.get(date));ix=bisect.bisect_right(fd,date)-1
            factor=ff[ix][1]/anchor if ix>=0 else None
            factors_ok=factor is not None and factor>0 and math.isfinite(factor)
            e={**ce,'open_eligibility':news[code]['status'],'price_status':reason,'factor_verified':factors_ok,
               'vcm_records':vcm.get((code,date),[]),'news_source_hash':digest(news[code]['sources']),
               'execution_liquidity':'daily_open_estimator_no_tick_queue_size_proof','suspension':[]}
            evidence.append(e);elig.append(dict(code=code,**e))
            quality.append(dict(code=code,date=date,adopted_source=reason,adopted=b, rejected_sources=['Yahoo'] if reason=='third_source_supports_Tencent' else [],
                Yahoo=yahoo.get(date),Tencent=vendor.get(date),Sina=sina.get(date) if reason!='two_vendor_agreement' else None))
            if b is None or not factors_ok:bars.append(None);continue
            bars.append(dict(date=date,index=i,factor=factor,vol=b['volume'],raw={k:b[k] for k in FIELDS},
                **{k:b[k]*factor for k in FIELDS},open_at=e['open_at'],close_at=e['close_at'],evidence=e))
        p=dict(code=code,market='HK',channel='TREND',industry='unknown',industry_current_vintage='unknown',dates=dates,bars=bars,evidence=evidence,sources=refs,benchmark=benchmark)
        panels.append(p);write(out/f'panel-{code}.json',p);log(out,'HK_panel',code=code,bars=sum(b is not None for b in bars),news_records=news[code]['records'])
    write(out/'price-decisions.json',quality);write(out/'session-eligibility.json',elig);write(out/'news-coverage.json',news)
    write(out/'vcm-coverage.json',dict(parsed_records=vcm_all,target_records=[dict(code=c,date=d,records=r) for (c,d),r in vcm.items() if c in CODES and '20210101'<=d<='20251231'],source_sha256=sha(vp)))
    return dict(panels=panels,dates=dates,failures=[])


def next_open(panel,signal,end,side,scenario,entry=None):
    if scenario not in ('hk_next_open_v1','hk_vcm_day_conservative_v1'):raise ValueError('not_HK_model')
    if panel['market']!='HK' or side not in ('buy','sell'):raise ValueError('HK_side_market')
    skipped=[]
    for i in range(signal+1,min(end,len(panel['bars']))):
        if entry is not None and i<entry:continue  # no short-before-buy; SAME session resale allowed
        b,e=panel['bars'][i],panel['evidence'][i]
        status=e.get('open_eligibility')
        if status=='verified_suspended':
            skipped.append(dict(index=i,date=e['date'],reason='official_suspended'));continue
        if status!='disclosure_reconstructed_no_halt':return dict(status='unknown',reason='open_eligibility_unknown',date=e['date'],skipped=skipped)
        if b is None or not e.get('factor_verified') or not valid_price({**b['raw'],'volume':b['vol']}):
            return dict(status='unknown',reason='price_or_factor_unknown',date=e['date'],skipped=skipped)
        if scenario=='hk_vcm_day_conservative_v1' and e.get('vcm_records'):
            skipped.append(dict(index=i,date=e['date'],reason='VCM_day_ex_post_stress_NOT_daily_limit'));continue
        return dict(status='filled',index=i,date=b['date'],price=b['open'],raw_price=b['raw']['open'],factor=b['factor'],side=side,
            model=scenario,open_at=e['open_at'],open_window=e['open_window'],realization_at=e['close_at'],evidence_index=i,skipped=skipped,
            settlement='HK_Tplus2_clearing_NO_resale_lock_cash_funded_event',timestamp_basis=e['timestamp_basis'],
            gap_from_signal_close=b['open']/panel['bars'][signal]['close']-1,source_hash=digest(panel['sources']))
    return dict(status='pending',reason='no_verified_open_before_fold_end',skipped=skipped)


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
            if e.get('open_eligibility')=='verified_suspended':
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
            cost=cfg['execution']['per_side_cost_bps']['HK']/10000
            values=[1]+[b['close']/(ent['price']*(1+cost)) for b in rows[start:min(end,len(rows))] if b]
            peak,dd=1,0
            for v in values:
                peak=max(peak,v);dd=min(dd,v/peak-1)
            r.update(unrealized_price_mark=values[-1]-1,unrealized_close_drawdown=dd,mark_date=mark['date'],
                     mark_basis='not_liquidated_entry_cost_only_not_realized_return')
        return {**r,'status':ex['status'] if ex else 'not_matured'}
    stop=ex['index']; cost=cfg['execution']['per_side_cost_bps']['HK']/10000
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
                            cc=copy.deepcopy(cfg);cc['execution']['per_side_cost_bps']['HK']=bps
                            u=replay_trade(p,i,end,rule,cc,scenario,breakout)
                        trades.append({**u,'group':group,'arm':arm,'scenario':scenario,'cost_bps':bps,
                            'code':p['code'],'fold':fold,'episode_id':eid,'market':'HK','industry':'unknown',
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



def cutoff(original,bundle,spec):
    panels={p['code']:p for p in bundle['panels']}
    folds={(f['code'],f['fold']):f for f in original['folds']};audits={(a['code'],a['fold']):a for a in original['audit']}
    events=[]
    for t in original['trades']:
        events.append(value_event(t,panels[t['code']],folds[t['code'],t['fold']]))
    opportunities=[]
    for scenario in spec['execution_scenarios']:
        for cost in spec['costs_bps_per_side']:
            ts=[r for r in events if r['scenario']==scenario and r['cost_bps']==cost and r['group']=='B']
            lookup={(r['code'],r['fold'],r['arm']):r for r in ts}
            for (code,fold),f in folds.items():
                audit=audits[code,fold]['opportunities']
                for arm in spec['trial_contract']['group_B']['entries']:
                    r=lookup.get((code,fold,arm))
                    if r is None:
                        unknown=bool(audit.get('unknown_or_warmup',0))
                        r=dict(code=code,fold=fold,group='B',arm=arm,scenario=scenario,cost_bps=cost,episode_id=None,
                            valuation_status='no_signal_unobservable_sessions' if unknown else 'no_signal_cash',
                            entry_event_value=None,capital_opportunity_value=None if unknown else 0.,
                            realized_component=None if unknown else 0.,unrealized_component=None if unknown else 0.,
                            full_path_drawdown=None if unknown else 0.,unpaid_sell_cost_sensitivity=None if unknown else 0.)
                    opportunities.append({**r,'opportunity_sessions':f['test_sessions'],
                        'unknown_or_warmup_sessions':audit.get('unknown_or_warmup',0),
                        'trigger_sessions':audit.get(arm+'|trigger',0),'source_opportunities':audit})
    summaries={};pairs={}
    for scenario in spec['execution_scenarios']:
        for cost in spec['costs_bps_per_side']:
            key=f'{scenario}|{cost}'
            es=[e for e in events if e['scenario']==scenario and e['cost_bps']==cost]
            bs=[e for e in opportunities if e['scenario']==scenario and e['cost_bps']==cost]
            for group in ('A','B'):
                arms=spec['trial_contract']['group_A']['exits'] if group=='A' else spec['trial_contract']['group_B']['entries']
                for arm in arms:
                    summaries[f'{key}|{group}|{arm}|entered_events']=summarize([e for e in es if e['group']==group and e['arm']==arm],'entry_event_value')
                    if group=='B':summaries[f'{key}|B|{arm}|capital_opportunities']=summarize([e for e in bs if e['arm']==arm],'capital_opportunity_value')
            for arm in ('structure_failure','structure_failure_atr'):
                pairs[f'{key}|A|{arm}']=compare_pairs(es,'old_ma60_5',arm,'A','entry_event_value')
            for arm in ('breakout','pullback'):
                pairs[f'{key}|B|{arm}']=compare_pairs(bs,'old_trigger',arm,'B','capital_opportunity_value')
    strata=defaultdict(list)
    for e in events:
        for field in ('code','fold','market','industry','industry_current_vintage','volatility','regime'):
            if field in e:strata[f"{e['scenario']}|{e['cost_bps']}|{e['group']}|{e['arm']}|{field}|{e[field]}"].append(e)
    return dict(strata={k:summarize(v,'entry_event_value') for k,v in strata.items()},method='common_fold_cutoff_all_entered_event_valuation_v1',events=events,B_opportunities=opportunities,
        summary=summaries,paired=pairs,limitations=['not_PIT_selection','not_funded_portfolio','not_cash_dividend_total_return',
        'unknown_not_zero_or_dropped','exposed_missing_close_invalidates_full_path_drawdown','no_intraday_high_low_path','small_correlated_clusters'],
        input_results_digest=digest(original))



def report(result,valuation):
    def pct(x):return 'unknown' if x is None else f'{x*100:.4f}%'
    lines=['# HK 2021—2025 真实历史诊断（r3）','',
        '固定汇丰00005、港交所00388、中移动00941；不改原参数。9个共同OOS窗、27个股票×fold。',
        '当前vintage复权价格择时诊断；不是PIT选股、实际成交凭证或资金组合收益。',
        '成交为经session资格重建后的下一session日open估计；费用每边25/50/75bps。',
        '资格来自完整非空HKEXnews股票年度公告与专项分类交叉查询及官方市场例外；不是逐tick开盘状态认证。',
        '正常日09:20—09:30为开盘估计窗口，不伪造日open实际成交时间；延迟开市使用公告时间。',
        'T+2为清算周期，非禁售期；无CN固定涨跌停/T+1。VCM日保守情景为事后执行压力而非信号。','',
        '|基准25bps组/臂|事件数|闭合|闭合均值|截止有值/全事件|共同截止均值|平均/最差全路径回撤|',
        '|---|---:|---:|---:|---|---:|---|']
    for k,a in result['summary'].items():
        if k.startswith('hk_next_open_v1|25|'):
            v=valuation['summary'][k+'|entered_events']
            lines.append(f"|{'/'.join(k.split('|')[2:])}|{a['sample_count']}|{a['round_trips']}|{pct(a['mean_net_price_return'])}|{v['known']}/{v['total']}|{pct(v['mean_known'])}|{pct(v['mean_full_path_drawdown'])}/{pct(v['worst_full_path_drawdown'])}|")
    lines+=['','主比较使用共同截止全部事件：已退出转零息现金，未退出以精确fold末close估值；仅扣已发生费用。','',
        '|基准配对新版−旧版|已知/全分母|截止估值差|描述性95%区间|平均路径回撤差（正较好）|',
        '|---|---|---:|---|---:|']
    for k,p in valuation['paired'].items():
        if k.startswith('hk_next_open_v1|25|'):
            a=p['valuation'];ci=a['descriptive_95_interval'];lines.append(f"|{'/'.join(k.split('|')[2:])}|{a['known']}/{a['total']}|{pct(a['mean_known'])}|{' — '.join(map(pct,ci)) if ci else 'unknown'}|{pct(p['drawdown']['mean_known'])}|")
    lines+=['','B不是同入场因果比较；全部27个股票×fold资本机会分母含完整可观测的无信号现金和unknown。','',
        '|全费用/执行/组/臂/口径|有值/分母|截止均值|未退出假设卖出费敏感性|','|---|---|---:|---:|']
    for k,a in valuation['summary'].items():lines.append(f"|{k}|{a['known']}/{a['total']}|{pct(a['mean_known'])}|{pct(a['mean_hypothetical_sell_cost_sensitivity'])}|")
    lines+=['','卖飞20日、假突破10日均限定共同OOS边界；边界不足unknown。尾部、换手、分层、未知覆盖见full-results及common-cutoff-results。',
        '只有3个股票簇，2000次交叉股票×fold bootstrap仅为描述性；幸存者偏差/现时行业未知不因此消失。',
        '因子用新浪链归一到2025末，已有Yahoo链交叉核验；不是现金股息账户收益。手续费为冻结的混合费率情景，不含实际委托金额/手数/最低佣金逐笔计费。',
        '日open非盘口保证：没有队列、冲击、成交容量、券商账户限制或借贷复用假设。持有现金资金足额的独立事件，不声称真实组合可执行。']
    return '\n'.join(lines)+'\n'


def decode(out):
    # Collection-only dependency; decode archived encoded bytes, NEVER fetch here.
    import importlib
    m=importlib.import_module('akshare.stock.stock_hk_sina')
    for code in CODES:
        b=read_checked(out/'raw'/f'sina_daily_{code}.raw')
        js=m.MiniRacer();js.eval(m.hk_js_decode)
        rows=js.call('d',b.decode().split('=')[1].split(';')[0].replace('"',''))
        write(out/f'sina_daily_{code}.json',rows)


def verify(out):
    protected=json.loads((out/'protected-before.json').read_text())
    assert all(sha(ROOT/p)==h for p,h in protected.items())
    manifest=json.loads((out/'inputs-manifest.json').read_text())
    assert all(sha(ROOT/p)==h for p,h in manifest.items())
    original=json.loads((out/'full-results.json').read_text())
    panels={c:json.loads((out/f'panel-{c}.json').read_text()) for c in CODES}
    folded={(f['code'],f['fold']):f for f in original['folds']};closed=0;sets=defaultdict(list)
    for t in original['trades']:
        p=panels[t['code']];f=folded[t['code'],t['fold']];end=p['dates'].index(f['test_end'])+1
        for side,fill in [('buy',t['entry_fill']),('sell',t.get('exit_fill',{}))]:
            if fill.get('status')!='filled':continue
            sig=t['signal_index'] if side=='buy' else p['dates'].index(t['exit_signal_date'])
            assert fill==next_open(p,sig,end,side,t['scenario'])
            assert fill['date']<=f['test_end'] and fill['date']>p['dates'][sig]
        if t['status']=='closed':
            closed+=1;c=t['cost_bps']/10000
            assert math.isclose(t['net_price_return'],t['exit_fill']['price']*(1-c)/(t['entry_fill']['price']*(1+c))-1,abs_tol=1e-12)
        value_event(t,p,f)
        if t['group']=='A':sets[(t['episode_id'],t['scenario'],t['cost_bps'])].append(t)
    assert all(len(ts)==3 and len({json.dumps(t['entry_fill'],sort_keys=True) for t in ts})==1 for ts in sets.values())
    assert closed>0
    return dict(protected_files=len(protected),inputs=len(manifest),events=len(original['trades']),closed_rows_with_scenario_cost_duplicates=closed,
        A_same_entry_sets=len(sets),status='developer_verification_not_independent_acceptance')


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True);ap.add_argument('--decode',action='store_true');ap.add_argument('--verify',action='store_true')
    args=ap.parse_args();out=args.out.resolve()
    if out in (R1,R2) or R1 in out.parents or R2 in out.parents:raise ValueError('isolated_output_required')
    if args.verify:print(json.dumps(verify(out)));return
    frozen=json.loads((out/'frozen-contract.json').read_text())
    assert frozen['codes']==list(CODES) and frozen['trial_contract']==contract()
    for p,h in json.loads((out/'protected-before.json').read_text()).items():
        if sha(ROOT/p)!=h:raise ValueError('protected_artifact_changed:'+p)
    if args.decode:decode(out)
    log(out,'start_HK_replay',contract_sha256=sha(out/'frozen-contract.json'))
    # Validate all current input bytes against the pre-replay seal on subsequent runs.
    paths=list((out/'raw').glob('*'))+[out/f'sina_daily_{c}.json' for c in CODES]+[out/'frozen-contract.json']
    manifest={str(p.relative_to(ROOT)):sha(p) for p in paths if p.is_file()}
    seal=out/'inputs-manifest.json'
    if seal.exists() and json.loads(seal.read_text())!=manifest:raise ValueError('input_seal_changed')
    if not seal.exists():write(seal,manifest)
    bundle=assemble(out)
    spec={**frozen,'execution_scenarios':frozen['scenarios']}
    r=analyze(bundle,spec,out,'full');v=cutoff(r,bundle,spec)
    write(out/'common-cutoff-results.json',v);(out/'HK-RESULTS.md').write_text(report(r,v))
    write(out/'verification.json',verify(out))
    write(out/'run.json',dict(command=f'python3 -m mt1.hk_history --out {out.relative_to(ROOT)} --decode',
        implementation_sha256=sha(Path(__file__)),contract_sha256=sha(out/'frozen-contract.json'),results_sha256=sha(out/'full-results.json'),
        valuation_sha256=sha(out/'common-cutoff-results.json'),git_head=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()))
    log(out,'complete_HK_replay',events=len(r['trades']),closed=sum(t['status']=='closed' for t in r['trades']))


if __name__=='__main__':main()
