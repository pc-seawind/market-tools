"""Finite capture worker, stdlib HTTP only. NO broker/cron/config write API.

Preflight requests run concurrently, quote polling uses one batched HTTP call.
Every response (including HTTP/provider errors) is retained verbatim. Raw hashes
are not capture identities: identical bytes at two times keep distinct IDs.
"""
import concurrent.futures
import copy
import fcntl
import hashlib
import json
import os
import time
import urllib.request
import urllib.error
from datetime import datetime,timedelta,timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from . import action_execution as ex
from .action_loop import read,write,dump,now,snapshots,save,execute,transition,TERMINAL
from .action_recovery import ensure,renew,expire
from .scope import load,codes,DEFAULT
from .timing import instant,digest
from .timing_cli import file_hash,protected_hashes


class Collector:
    def __init__(self,out,deadline,clock=now,monotonic=time.monotonic):
        self.out=Path(out);self.out.mkdir(parents=True,exist_ok=True)
        self.deadline=deadline;self.clock=clock;self.monotonic=monotonic
    def fetch(self,name,url,api=None,params=None):
        started=self.clock();t=self.monotonic();params=params or {};status=None;raw=b'';error=None
        try:
            remaining=self.deadline-self.monotonic()
            if remaining<=0:raise TimeoutError('worker_deadline')
            headers={'User-Agent':'market-tools/mt13-readonly'};data=None
            if api:
                token=os.environ.get('TUSHARE_TOKEN')
                if not token:raise ValueError('missing_provider_credential')
                data=json.dumps({'api_name':api,'token':token,'params':params,'fields':''}).encode()
                headers['Content-Type']='application/json'
            request=urllib.request.Request(url,data=data,headers=headers)
            with urllib.request.urlopen(request,timeout=min(8,remaining)) as response:
                raw=response.read();status=response.status
        except urllib.error.HTTPError as e:
            raw=e.read();status=e.code;error='HTTP_'+str(e.code)
        except Exception as e:
            # No raw exception text: upstream errors can embed credential URLs.
            error='missing_provider_credential' if str(e)=='missing_provider_credential' else type(e).__name__
        fetched=self.clock();path=self.out/(name+'.raw')
        with path.open('xb') as f:f.write(raw)
        m={'name':path.name,'path':str(path.resolve()),'url':url,'api':api,'params':params,
           'method':'POST' if api else 'GET','started_at':started,'fetched_at':fetched,
           'http_status':status,'transport_error':error,'duration_seconds':self.monotonic()-t,
           'sha256':hashlib.sha256(raw).hexdigest(),'representation':'exact_provider_response_bytes',
           'request_body_not_archived':'provider credential omitted'}
        m['source_id']=digest([m['sha256'],started,fetched,url,params])
        write(self.out/(name+'.meta.json'),m)
        return m


def preflight(collector,items,anchors,day):
    ds=day.replace('-','');end=(datetime.fromisoformat(day)+timedelta(days=32)).strftime('%Y%m%d')
    starts=[o['signal_date'] for o in anchors.values()]
    begin=min(starts+[day]).replace('-','')
    tasks=[]
    for market in sorted({i['market'] for i in items.values()}):
        api='trade_cal' if market=='CN' else 'hk_tradecal'
        tasks.append(('calendar_'+market,'https://api.tushare.pro',api,
                      {'start_date':begin,'end_date':end,**({'exchange':'SSE'} if market=='CN' else {})}))
    if any(i['market']=='CN' for i in items.values()):
        tasks.extend([('suspensions_CN','https://api.tushare.pro','suspend_d',{'trade_date':ds,'suspend_type':'S'}),
                      ('limits_CN','https://api.tushare.pro','stk_limit',{'trade_date':ds})])
    for code,item in items.items():
        if item['market']=='CN':tasks.append(('factor_'+code,'https://api.tushare.pro','adj_factor',{'ts_code':code,'start_date':begin,'end_date':ds}))
        else:tasks.append(('factor_'+code,f'https://finance.sina.com.cn/stock/hkstock/{code.split(".")[0]}/qfq.js',None,{}))
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        values=list(pool.map(lambda t:collector.fetch(*t),tasks))
    return {t[0]:m for t,m in zip(tasks,values)}


def mapping(records):
    result={}
    for m in records:
        result[m['source_id']]=m;result[m['sha256']]=m
    return result


def latest_panels(root):
    # Intraday execution-only commits do not replace the last completed data.
    for path,_ in reversed(snapshots(root)):
        bundle=read(Path(path).parent/'materials/bundle.json')
        if bundle.get('panels'):return {p['code']:p for p in bundle['panels']}
    return {}


def anchors_for(scope,panels):
    result={}
    for code,item in {**scope['candidates'],**scope['recommendations'],**scope['holdings']}.items():
        p=panels.get(code)
        if not p or not p.get('bars') or p.get('adjustment')!='vendor_factor_verified':continue
        b=p['bars'][-1]
        result[code]={'code':code,'market':p['market'],'side':'BUY','signal_id':'PROBE_ONLY_NOT_A_SIGNAL',
                      'signal_date':b['date'],'signal_price':b['close'],'signal_adjusted_price':b['close']*b['factor'],
                      'triggered_at':b['close_at'],'basis_id':p['basis_id']}
    return result


def pause(root,scope_path,sid,reason,resume=False):
    if not reason:raise ValueError('operator_reason_required')
    root=Path(root)
    with (root/'.action.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX);prior=snapshots(root);s=copy.deepcopy(prior[-1][1]);o=s['ledger'][sid]
        if load(scope_path)['epoch']!=s['scope_epoch']:raise ValueError('scope_changed')
        if o['execution_status'] in ('filled','cancelled'):raise ValueError('terminal_signal_not_resumable')
        if bool(o.get('operator_paused'))==(not resume):return {'idempotent':True}
        s['asof']=max(now(),s['asof'],key=instant);s['transitions']=[]
        o['operator_paused']=not resume
        o.setdefault('operator_events',[]).append({'at':s['asof'],'action':'resume' if resume else 'pause','reason':reason})
        if not resume:
            o['status_before_pause']=o['execution_status'];transition(s,o,'paused','operator_pause:'+reason,s['asof'])
        else:
            o['execution_not_before']=s['asof']
            transition(s,o,o.pop('status_before_pause','pending'),'operator_resume:'+reason,s['asof'])
        return save(root,s,{'operator':o['operator_events'][-1]},scope_path,digest([sid,s['sequence'],reason,resume])[:24])


def consume(bundle_path,scope_path,root):
    """Execution-only ledger transaction: never creates a technical signal.

    Reads root under lock AFTER network finishes, so concurrent cancellation or
    another worker fill always wins. The same capture ID commits only once.
    """
    root=Path(root);b=read(bundle_path);scope=load(scope_path);protected=protected_hashes(scope_path)
    with (root/'.action.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX);prior=snapshots(root)
        if not prior:raise ValueError('observation_root_required_no_automatic_seed')
        rid=digest(['execution-only-v1',b,file_hash(__file__),file_hash(Path(ex.__file__)),file_hash(Path(__file__).with_name('action_recovery.py'))])[:24]
        for path,r in prior:
            if r['run_id']==rid:return {'manifest':path,'idempotent':True}
        s=copy.deepcopy(prior[-1][1]);asof=b['asof']
        if b['scope_epoch']!=s['scope_epoch'] or scope['epoch']!=s['scope_epoch'] or set(b['scope_codes'])!=codes(scope):raise ValueError('execution_scope_changed')
        if b['source_kind']!=('SYNTHETIC_ONLY' if s['synthetic'] else 'real_current_readonly_collection'):raise ValueError('execution_synthetic_mismatch')
        if b['execution_model']!=s.get('execution_model','strict-open-v1'):raise ValueError('execution_model_requires_separate_root')
        if instant(asof)<instant(s['asof']):raise ValueError('execution_capture_before_last_transaction')
        if not s['synthetic'] and (instant(asof)>instant(now()) or (instant(now())-instant(asof)).total_seconds()>1800):raise ValueError('execution_capture_stale_or_future')
        sources=mapping(b['inputs']);materials=[]
        for i,m in enumerate(b['inputs']):
            if file_hash(m['path'])!=m['sha256']:raise ValueError('execution_source_bytes_changed')
            materials.append({'name':f'execution-source-{i:03}.raw','path':m['path'],'fetched_at':m['fetched_at'],'representation':m['representation']})
        contract_hash=digest(ex.MODELS[b['execution_model']])
        if s.get('execution_contract_sha256',contract_hash)!=contract_hash:raise ValueError('execution_contract_changed')
        s['execution_contract_sha256']=contract_hash
        s.update(asof=asof,transitions=[])
        for o in s['ledger'].values():
            renew(s,o,asof)
            if o['execution_status'] in TERMINAL or o.get('operator_paused'):continue
            a=ensure(o);cfg=s['policies'][o['version']]
            # Use raw exchange calendar for TTL, never weekdays/capture count.
            try:
                cref=b['calendars'][o['market']]
                rows,_=ex.api_rows(cref,sources,'trade_cal' if o['market']=='CN' else 'hk_tradecal')
                ds=sorted(r['cal_date'] for r in rows if str(r['is_open'])=='1' and r['cal_date']>a['after_session'].replace('-',''))
                allowed=[datetime.strptime(d,'%Y%m%d').date().isoformat() for d in ds[:cfg['order_ttl_sessions']]]
                quotes=[q for q in b['execution_quotes'] if q['target_signal_id']==o['signal_id'] and q['date'] in allowed]
                execute(s,o,quotes,asof,sources,cfg)
                completed=[d for d in allowed if instant(d+('T15:00:00+08:00' if o['market']=='CN' else 'T17:00:00+08:00'))<=instant(asof)]
                if len(completed)>=cfg['order_ttl_sessions'] and o['execution_status'] not in TERMINAL:expire(s,o,asof,completed[-1])
                elif not quotes and o['execution_status'] not in TERMINAL:
                    reason=b.get('capture_status','capture_not_available_retry_next_session')
                    transition(s,o,'blocked',reason,asof)
            except (KeyError,ValueError,TypeError):transition(s,o,'blocked','calendar_or_provider_unavailable_retry_next_run',asof)
            o['next_recovery']='next bounded capture; expired SELL requires recent risk reconfirmation; operator pause/cancel never auto resumes'
        for c in s['cards']:
            refs=[s['ledger'][x] for x in c['signal_ids']]
            if any(o.get('fill') and o['fill']['at']>c['triggered_at'] for o in refs):
                if c['code'] not in scope['holdings']:
                    c['action']='HOLD' if c['version']+'|'+c['code'] in s['positions'] else 'WAIT'
                c['execution_only_update']=True
        for module in (Path(__file__),Path(ex.__file__),Path(__file__).with_name('action_recovery.py')):
            materials.append({'name':module.name,'path':str(module)})
        s['protected_unchanged']=protected==protected_hashes(scope_path)
        if not s['protected_unchanged']:raise ValueError('protected_inputs_changed')
        return save(root,s,b,scope_path,rid,materials)


def run_worker(scope_path,root,out,mode='probe',max_seconds=240,poll_seconds=3,probe_model=None):
    if not 1<=max_seconds<=1800 or not 1<=poll_seconds<=60:raise ValueError('bounded_worker_limits_required')
    out=Path(out);out.mkdir(parents=True,exist_ok=False)
    started=now();t=time.monotonic();deadline=t+max_seconds;collector=Collector(out/'sources',deadline)
    scope=load(scope_path);panels=latest_panels(root);anchors=anchors_for(scope,panels)
    items={**scope['candidates'],**scope['recommendations'],**scope['holdings']}
    for code,item in items.items():
        item.setdefault('market','HK' if code.endswith('.HK') else 'CN')
        if item['market'] not in ('CN','HK'):raise ValueError('unsupported_capture_market')
    prior=snapshots(root);model=prior[-1][1].get('execution_model','strict-open-v1')
    if probe_model:
        if mode!='probe' or probe_model not in ex.MODELS:raise ValueError('model_override_probe_only')
        model=probe_model
    if prior[-1][1]['synthetic']:raise ValueError('live_worker_refuses_synthetic_ledger')
    day=str(instant(started).astimezone(ZoneInfo('Asia/Shanghai')).date())
    pre=preflight(collector,items,anchors,day);records=list(pre.values());sources=mapping(records)
    opened={};next_sessions={};errors={}
    for market in {i['market'] for i in items.values()}:
        try:opened[market],next_sessions[market],_=ex.calendar(pre['calendar_'+market]['source_id'],sources,market,day)
        except Exception as e:opened[market]=None;errors[market]=type(e).__name__+':'+str(e)
    symbol=','.join(code.split('.')[1].lower()+code.split('.')[0] for code in sorted(items))
    previous=None;receipts=[];outputs=[];i=0;current_status='initializing'
    while time.monotonic()<deadline:
        qm=collector.fetch(f'quote-{i:03}','https://qt.gtimg.cn/q='+symbol);records.append(qm);sources=mapping(records)
        latest=snapshots(root)[-1][1]
        orders=[o for o in latest['ledger'].values() if o['execution_status'] not in ('filled','cancelled') and not o.get('operator_paused')]
        normalized=[];probes=[]
        for order in orders+list(anchors.values()):
            refs={'quote':qm['source_id'],'calendar':pre['calendar_'+order['market']]['source_id'],
                  'factor':pre['factor_'+order['code']]['source_id']}
            if previous:refs['previous_quote']=previous['source_id']
            if order['market']=='CN':refs.update(suspensions=pre['suspensions_CN']['source_id'],limits=pre['limits_CN']['source_id'])
            try:q=ex.derive(order,refs,sources,model)
            except Exception as e:q={'code':order['code'],'reason':str(e),'provider_adapter':'tencent-raw-v1'}
            if order['signal_id']=='PROBE_ONLY_NOT_A_SIGNAL':probes.append(q)
            elif 'target_signal_id' in q:normalized.append(q)
        at=now();local=instant(at).astimezone(ZoneInfo('Asia/Shanghai'))
        opening=local.replace(hour=9,minute=30,second=0,microsecond=0)
        if all(v is False for v in opened.values()):current_status='market_closed_next_verified_session'
        elif not any(v is True for v in opened.values()):current_status='calendar_unavailable_retry_next_run'
        elif local<opening:current_status='preopen_captured_waiting_for_window'
        elif model=='strict-open-v1' and local>opening+timedelta(seconds=60):current_status='strict_window_missed_retry_next_session'
        elif local>=opening+timedelta(hours=2,minutes=30):current_status='morning_window_finished_retry_next_session'
        elif orders and not normalized:current_status='quote_transport_or_schema_failed_retry'
        elif normalized and all(q.get('reason') for q in normalized):current_status='captured_with_hard_execution_gaps'
        else:current_status='captured' if normalized else 'no_natural_signal_probe_only'
        b={'source_kind':'real_current_readonly_collection','scope_epoch':scope['epoch'],'scope_codes':sorted(codes(scope)),
           'asof':at,'execution_model':model,'capture_status':current_status,'inputs':records.copy(),
           'calendars':{m:pre['calendar_'+m]['source_id'] for m in opened},'execution_quotes':normalized,
           'probe_only_not_signals':probes,'next_sessions':next_sessions,'errors':errors}
        path=out/f'execution-{i:03}.json';write(path,b);outputs.append(str(path))
        if mode!='probe':receipts.append(consume(path,scope_path,root))
        previous=qm;i+=1
        stop_status=current_status in ('market_closed_next_verified_session','calendar_unavailable_retry_next_run','strict_window_missed_retry_next_session','morning_window_finished_retry_next_session')
        if mode=='once' or (mode!='probe' and stop_status) or (mode=='probe' and i>=3):break
        remaining=deadline-time.monotonic()
        if remaining<=0:break
        # Wait in small bounded steps; never inherit daily collector's latency.
        time.sleep(min(poll_seconds,remaining))
    result={'started_at':started,'finished_at':now(),'duration_seconds':time.monotonic()-t,'max_seconds':max_seconds,
            'mode':mode,'execution_model':model,'status':current_status,'bundles':outputs,'receipts':receipts,
            'preflight_wall_seconds':(max(instant(m['fetched_at']) for m in pre.values())-min(instant(m['started_at']) for m in pre.values())).total_seconds(),
            'preflight_max_single_request_seconds':max((m['duration_seconds'] for m in pre.values()),default=0),
            'interface_checks':[{'name':m['name'],'http_status':m['http_status'],'transport_error':m['transport_error'],'duration_seconds':m['duration_seconds'],'sha256':m['sha256']} for m in records],
            'quote_latencies_seconds':[m['duration_seconds'] for m in records if m.get('url','').startswith('https://qt.')],
            'next_sessions':next_sessions,'errors':errors,'not_deployed':True,'natural_live_open_validation':'pending_first_legal_window',
            'recovery':'rerun finite worker next verified session; preflight permission/rate errors are explicit, not confidence veto'}
    write(out/'worker-result.json',result);return result
