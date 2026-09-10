"""Immutable diagnostic cohorts and fail-closed 20/40/60-session harvest.

NOT fills, holdings or efficacy certification. Raw prices only when corporate
and listing status evidence is complete. Unknowns never become zero returns.
"""
import fcntl
import gzip
import hashlib
import json
from datetime import datetime,timedelta,time,timezone
from pathlib import Path
from .review_time import instant,CN
from .candidates import day,number
from .data import api
from .store import digest

CONTRACT={
    'version':'mt11-forward-1','scope':'diagnostic_discovery_including_risk_vetoes_not_recommendations',
    'entry':'next_exchange_open_strictly_after_decision; raw_open',
    'horizons':[20,40,60],'maturity':'entry_session_index + horizon; exit raw_close',
    'benchmark':'000300.SH same entry open/exit close price index, not total return or tradable fund',
    'entry_cost_bps':10,'exit_cost_bps':10,
    'cost_basis':'illustrative all-in one-way scenario incl fees/slippage; not actual tax/commission or validated assumptions',
    'capital':'one independent unit per stock; after exit stays zero-interest cash, no reinvestment/rebalancing',
    'corporate_actions':'any action or adjustment change blocks; no invented dividend/share/tax treatment',
    'aggregation':'equal-weight descriptive only if entire frozen cohort complete; unknowns not dropped',
    'actual_holdings':False,'strategy_validated':False,
}


def save_new(path,obj):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x') as f:json.dump(obj,f,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False)


def calendar_days(calendar,decision):
    decision=instant(decision)
    fetched=instant(calendar['fetched_at'])
    if not timedelta(0)<=decision-fetched<=timedelta(days=7):raise ValueError('calendar future/stale')
    if calendar.get('exchange')!='SSE':raise ValueError('calendar exchange')
    rows=calendar['rows'];mapping={r['cal_date']:r['is_open'] for r in rows}
    if len(rows)!=len(mapping) or any(v not in ('0','1') for v in mapping.values()):raise ValueError('calendar duplicate/status')
    if not mapping:raise ValueError('calendar empty')
    start,end=day(min(mapping)),day(max(mapping))
    for i in range((end-start).days+1):
        if (start+timedelta(days=i)).strftime('%Y%m%d') not in mapping:raise ValueError('calendar hole')
    if decision.astimezone(CN).strftime('%Y%m%d') not in mapping:raise ValueError('calendar does not cover evaluation date')
    return sorted(d for d,v in mapping.items() if v=='1')


def schedule(price_asof,decision_at,calendar,observed_at):
    days=calendar_days(calendar,observed_at);decision=instant(decision_at)
    if instant(observed_at)<decision:raise ValueError('evaluation before decision')
    priceclose=datetime.combine(day(price_asof),time(15),CN)
    if priceclose>decision or price_asof not in days:raise ValueError('invalid starting price date')
    entries=[d for d in days if datetime.combine(day(d),time(9,30),CN)>decision]
    if not entries:raise ValueError('calendar lacks future entry')
    entry=entries[0];idx=days.index(entry)
    return {'entry_date':entry,'maturity_dates':{str(h):days[idx+h] if idx+h<len(days) else None for h in CONTRACT['horizons']}}


def register(root,source,calendar,observed_at):
    root=Path(root);source=Path(source)
    s=json.loads((source/'summary.json').read_text())
    # Stable key: one frozen selection per price date and method, not one per rerun timestamp.
    key=digest([s['asof'],s['method'],CONTRACT])[:24]
    path=root/'cohorts'/(key+'.json')
    if hashlib.sha256((source/'funnel.json.gz').read_bytes()).hexdigest()!=s['frozen_hashes']['funnel.json.gz']:
        raise ValueError('starting funnel hash mismatch')
    with gzip.open(source/'funnel.json.gz','rt') as f:rows=json.load(f)
    selected=sorted([{'code':r['code'],'name':r['name'],'channels':r['channels'],'risk':r['risk'],
                      'timing':r['timing'],'starting_close':(r['technical'] or {}).get('close')} for r in rows if r['channels']],key=lambda r:r['code'])
    codes=[r['code'] for r in selected]
    if len(codes)!=len(set(codes)):raise ValueError('duplicate cohort code')
    if path.exists():
        old=json.loads(path.read_text())
        if [r['code'] for r in old['members']]!=codes:raise ValueError('cohort membership changed; explicit new method/episode required')
        return old
    dates=schedule(s['asof'],s['decision_at'],calendar,observed_at)
    if instant(observed_at)>=datetime.combine(day(dates['entry_date']),time(9,30),CN):
        raise ValueError('late_registration_not_forward_cohort')
    cohort={'cohort_id':key,'price_asof':s['asof'],'decision_at':s['decision_at'],
            'registered_at':observed_at,'method':s['method'],'code_hashes':s['code_hashes'],
            'contract':CONTRACT,'members':selected,'schedule':dates,
            'source_run':str(source.resolve()),'source_hashes':s['frozen_hashes'],
            'calendar_at_registration':calendar,'starting_input_sha256':hashlib.sha256((source/'funnel.json.gz').read_bytes()).hexdigest()}
    # Copy exact frozen initial observations, so pruning old run dirs cannot lose inception.
    target=root/'cohorts'/(key+'-start.json.gz');target.parent.mkdir(parents=True,exist_ok=True)
    if not target.exists():
        with target.open('xb') as f:f.write((source/'funnel.json.gz').read_bytes())
    if hashlib.sha256(target.read_bytes()).hexdigest()!=cohort['starting_input_sha256']:
        raise ValueError('incomplete or changed inception archive')
    save_new(path,cohort)
    return cohort


def source_clearance(evidence,code,start,end,observed_at):
    """Must be explicit, sourced per issuer/window. Empty list is NOT clearance."""
    if not evidence:return ['issuer_actions_and_status_evidence_missing']
    try:
        if evidence['code']!=code or evidence['start_date']>start or evidence['end_date']<end:raise ValueError()
        stamp=instant(evidence['checked_at'])
        if not datetime.combine(day(end),time(15),CN)<=stamp<=instant(observed_at):raise ValueError()
        if evidence['listing_status']!='listed':return ['delisted_or_listing_status_not_clear']
        if evidence['actions_status']!='none_verified':return ['corporate_actions_unresolved']
        if evidence['suspension_status']!='none_verified':return ['suspension_or_status_unresolved']
        if not evidence['sources']:raise ValueError()
        from urllib.parse import urlparse
        for src in evidence['sources']:
            if instant(src['published_at'],date_bound='end')>stamp:raise ValueError()
            if urlparse(src['url']).scheme not in ('https','http') or not urlparse(src['url']).netloc:raise ValueError()
            if hashlib.sha256(Path(src['path']).read_bytes()).hexdigest()!=src['sha256']:raise ValueError()
        return []
    except (KeyError,TypeError,ValueError,OSError):return ['issuer_clearance_invalid']


def calculate(cohort,code,horizon,data,observed_at,days):
    dates=cohort['schedule'];entry=dates['entry_date'];end=dates['maturity_dates'][str(horizon)]
    base={'code':code,'horizon':horizon,'entry_date':entry,'maturity_date':end,
          'gross_return':None,'net_return':None,'benchmark_return':None,'excess_return':None}
    if end is None:return {**base,'status':'blocked','reasons':['calendar_horizon_unknown']}
    if datetime.combine(day(end),time(15),CN)>instant(observed_at):return {**base,'status':'not_matured','reasons':[]}
    if not data:return {**base,'status':'blocked','reasons':['mature_price_panel_missing']}
    errors=source_clearance(data.get('clearances',{}).get(code),code,entry,end,observed_at)
    def table(rows,label):
        out={}
        for r in rows:
            d=r['trade_date']
            if d in out:errors.append(label+'_duplicate')
            out[d]=r
        return out
    try:
        bars=table(data.get('daily',{}).get(code,[]),'price')
        factors=table(data.get('factors',{}).get(code,[]),'factor')
        bench=table(data.get('benchmark',[]),'benchmark')
        window=[d for d in days if entry<=d<=end]
        if len(window)!=horizon+1:errors.append('session_count_changed')
        for d in window:
            if d not in bars or d not in factors or d not in bench:errors.append('price_factor_or_benchmark_session_missing');continue
            r=bars[d];f=factors[d]
            if r.get('ts_code')!=code or f.get('ts_code')!=code:errors.append('foreign_symbol')
            if any(number(r.get(k)) is None or number(r[k])<=0 for k in ('open','close','vol')):errors.append('invalid_or_zero_volume')
            if number(f.get('adj_factor')) is None or number(f['adj_factor'])<=0:errors.append('invalid_factor')
            if any(number(bench[d].get(k)) is None or number(bench[d][k])<=0 for k in ('open','close')):errors.append('benchmark_invalid')
        if any(datetime.combine(day(d),time(15),CN)>instant(observed_at) for d in list(bars)+list(factors)+list(bench)):
            errors.append('future_unclosed_panel')
        if len({factors[d]['adj_factor'] for d in window if d in factors})!=1:errors.append('adjustment_change_or_unknown')
        if errors:return {**base,'status':'blocked','reasons':sorted(set(errors))}
        op=number(bars[entry]['open']);cl=number(bars[end]['close']);gross=cl/op-1
        costs=cohort['contract'];net=cl*(1-costs['exit_cost_bps']/10000)/(op*(1+costs['entry_cost_bps']/10000))-1
        br=number(bench[end]['close'])/number(bench[entry]['open'])-1
        return {**base,'status':'observed_diagnostic','reasons':[],'gross_return':gross,'net_return':net,'benchmark_return':br,'excess_return':net-br}
    except (KeyError,TypeError,ValueError,ZeroDivisionError):return {**base,'status':'blocked','reasons':['panel_invalid']}


def harvest(cohort,calendar,observed_at,data=None):
    if cohort['contract']!=CONTRACT:raise ValueError('unsupported/altered observation contract')
    days=calendar_days(calendar,observed_at)
    current=schedule(cohort['price_asof'],cohort['decision_at'],calendar,observed_at)
    changed=current!=cohort['schedule']
    rows=[]
    for member in cohort['members']:
        for h in cohort['contract']['horizons']:
            if changed:
                rows.append({'code':member['code'],'horizon':h,'status':'blocked','reasons':['calendar_schedule_changed_research_required'],'net_return':None})
            else:rows.append(calculate(cohort,member['code'],h,data,observed_at,days))
    aggregates={}
    for h in cohort['contract']['horizons']:
        group=[r for r in rows if r['horizon']==h];statuses={k:sum(r['status']==k for r in group) for k in ('not_matured','blocked','observed_diagnostic')}
        aggregates[str(h)]={'counts':statuses,'equal_weight_net_return':sum(r['net_return'] for r in group)/len(group) if group and statuses['observed_diagnostic']==len(group) else None,
                            'not_a_strategy_or_actual_portfolio':True}
    return {'cohort_id':cohort['cohort_id'],'observed_at':observed_at,'contract':cohort['contract'],
            'schedule':cohort['schedule'],'rows':rows,'aggregates':aggregates,'strategy_validated':False}


def harvest_id(cohort,calendar,observed_at,data):
    # Same inputs and same completed-date evaluation have same identity, even if
    # wall clock or calendar fetched_at changes. Mature results are never overwritten.
    local=instant(observed_at).astimezone(CN)
    boundary=local.strftime('%Y%m%d')+('-closed' if local.hour>=15 else '-preclose')
    return digest([cohort,calendar['rows'],boundary,data,hashlib.sha256(Path(__file__).read_bytes()).hexdigest()])[:32]


def run(root,source,panel=None):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    with (root/'lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        now=datetime.now(timezone.utc);local=now.astimezone(CN);stamp=now.isoformat()
        # Registry can span many dates. Fetch a complete calendar covering oldest
        # inception and future expected maturities; no weekday approximation.
        existing=[json.loads(p.read_text()) for p in (root/'cohorts').glob('*.json')]
        source_summary=json.loads((Path(source)/'summary.json').read_text())
        first=min([c['price_asof'] for c in existing]+[source_summary['asof']])
        calpath=root/'calendars'/(local.strftime('%Y%m%d')+'-'+first+'.json')
        if calpath.exists():calendar=json.loads(calpath.read_text())
        else:
            rows=api('trade_cal',exchange='SSE',start_date=first,end_date=(local.date()+timedelta(days=240)).strftime('%Y%m%d'))
            calendar={'exchange':'SSE','source':'Tushare trade_cal','fetched_at':datetime.now(timezone.utc).isoformat(),'rows':rows}
            save_new(calpath,calendar)
        stamp=datetime.now(timezone.utc).isoformat()
        register(root,source,calendar,stamp)
        data=None
        # Current cycle's 120-day panel covers ordinary 60-session windows.
        # If an old cohort/window is outside it, missing sessions block, not impute.
        registered=[json.loads(p.read_text()) for p in (root/'cohorts').glob('*.json')]
        needs_panel=any(d and datetime.combine(day(d),time(15),CN)<=instant(stamp) for c in registered for d in c['schedule']['maturity_dates'].values())
        if panel and needs_panel:
            pricepath=Path(source)/'price-input.json.gz'
            if hashlib.sha256(pricepath.read_bytes()).hexdigest()!=source_summary['frozen_hashes']['price-input.json.gz']:
                raise ValueError('harvest panel source hash mismatch')
            with gzip.open(pricepath,'rt') as f:data=json.load(f)
            data['clearances']={}
            clearance_path=root/'issuer-clearances.json'
            if clearance_path.exists():data['clearances']=json.loads(clearance_path.read_text())
            data['evidence_bytes_hashes']={}
            for evidence in data['clearances'].values():
                for src in evidence.get('sources',[]):
                    try:
                        raw=Path(src['path']).read_bytes();h=hashlib.sha256(raw).hexdigest()
                        dest=root/'evidence-blobs'/h;dest.parent.mkdir(parents=True,exist_ok=True)
                        if not dest.exists():
                            with dest.open('xb') as f:f.write(raw)
                        data['evidence_bytes_hashes'][src['path']]=h
                    except OSError:data['evidence_bytes_hashes'][src.get('path','unknown')]='missing'
            input_key=digest(data)
            inputpath=root/'inputs'/(input_key+'.json.gz');inputpath.parent.mkdir(parents=True,exist_ok=True)
            if not inputpath.exists():
                with gzip.open(inputpath,'wt') as f:json.dump(data,f,ensure_ascii=False,allow_nan=False)
        outcomes=[]
        for path in sorted((root/'cohorts').glob('*.json')):
            cohort=json.loads(path.read_text())
            identity=harvest_id(cohort,calendar,stamp,data)
            out=root/'harvests'/cohort['cohort_id']/(identity+'.json')
            if out.exists():result=json.loads(out.read_text())
            else:
                result=harvest(cohort,calendar,stamp,data)
                result['input_hashes']={'cohort':hashlib.sha256(path.read_bytes()).hexdigest(),'calendar':hashlib.sha256(calpath.read_bytes()).hexdigest(),'panel':digest(data)}
                result['artifact_id']=identity
                result['harvester_code_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
                result['frozen_panel_input']=str(inputpath.resolve()) if data else None
                save_new(out,result)
            outcomes.append({'cohort_id':cohort['cohort_id'],'artifact':str(out.resolve()),'schedule':cohort['schedule'],'aggregates':result['aggregates']})
        receipt={'functionality':'automatic_registration_scheduling_and_harvest_with_fail_closed_inputs','cohorts':outcomes,'evaluation_time':stamp,'mode':'diagnostic_forward_only','natural_dispatch_verified':False}
        return receipt
