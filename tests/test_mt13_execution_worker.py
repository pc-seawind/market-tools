"""New-provider fixtures are SYNTHETIC_ONLY; no mocked timestamps in real probes."""
import copy
import hashlib
import json
from datetime import datetime,timedelta
from pathlib import Path
import pytest
from mt1 import action_execution as ex
from mt1.action_loop import read,write,snapshots,observe,cancel
from mt1.action_worker import consume,mapping,Collector,pause
from mt1.action_demo import fixture,append,bundle
from mt1.timing import digest
from test_mt13_action_loop import Run


def record(root,name,raw,at,api=None,params=None,url=None):
    if isinstance(raw,dict):raw=json.dumps(raw).encode()
    path=root/(name+'.raw');path.write_bytes(raw)
    m={'path':str(path),'sha256':hashlib.sha256(raw).hexdigest(),'started_at':at,'fetched_at':at,
       'http_status':200,'api':api,'params':params or {},'url':url or ('https://api.tushare.pro' if api else 'https://qt.gtimg.cn/q=sz001309'),
       'representation':'SYNTHETIC_PROVIDER_RESPONSE','synthetic':True}
    m['source_id']=digest([m['sha256'],at,name]);return m


def response(rows,fields=None):
    fields=fields or list(rows[0])
    return {'code':0,'data':{'fields':fields,'items':[[r.get(f) for f in fields] for r in rows]}}


def quote_raw(code,stamp,vol=201,last=109,opening=108.5):
    num,market=code.split('.');a=['0']*(78 if market=='HK' else 88)
    for i,v in {0:100 if market=='HK' else 51,1:'合成样本',2:num,3:last,4:108,5:opening,6:vol,
                30:stamp,33:200,34:1,47:1000,48:1}.items():a[i]=str(v)
    return ('v_'+market.lower()+num+'="'+'~'.join(a)+'";').encode('gbk')


def evidence(tmp,order,day,model='strict-open-v1',capture='09:30:05',pre='09:25:00'):
    tmp.mkdir(exist_ok=True);code=order['code'];market=order['market'];ds=day.replace('-','');records=[];refs={}
    def add(key,raw,at,api=None,params=None,url=None):
        m=record(tmp,key,raw,day+'T'+at+'+08:00',api,params,url);records.append(m);refs[key]=m['source_id'];return m
    dates=[];d=datetime.fromisoformat(order['signal_date'])
    while str(d.date())<=day:
        dates.append({'cal_date':d.strftime('%Y%m%d'),'is_open':int(d.weekday()<5)})
        d+=timedelta(days=1)
    add('calendar',response(dates),pre,'trade_cal' if market=='CN' else 'hk_tradecal')
    if market=='CN':
        add('factor',response([{'ts_code':code,'trade_date':order['signal_date'].replace('-',''),'adj_factor':1},
                               {'ts_code':code,'trade_date':ds,'adj_factor':1}]),pre,'adj_factor')
        add('suspensions',response([],['ts_code','trade_date','suspend_type']),pre,'suspend_d',{'trade_date':ds,'suspend_type':'S'})
        add('limits',response([{'ts_code':code,'trade_date':ds,'up_limit':1000,'down_limit':1}]),pre,'stk_limit')
    else:
        add('factor',b'var f={"data":[{"d":"2020-01-01","f":"1"}]};',pre,url=f'https://finance.sina.com.cn/stock/hkstock/{code.split(".")[0]}/qfq.js')
    def stamp(t):return (day.replace('-','/')+' '+t) if market=='HK' else ds+t.replace(':','')
    add('previous_quote',quote_raw(code,stamp('09:30:01'),200), '09:30:02',url='https://qt.gtimg.cn/q='+market.lower()+code.split('.')[0])
    add('quote',quote_raw(code,stamp('09:30:03')),capture,url='https://qt.gtimg.cn/q='+market.lower()+code.split('.')[0])
    sources=mapping(records);q=ex.derive(order,refs,sources,model)
    return q,records,sources


def order(market='CN',model='strict-open-v1'):
    return {'code':'001309.SZ' if market=='CN' else '00700.HK','market':market,'signal_id':'SYNTHETIC-test',
            'side':'BUY','signal_date':'2026-09-15','triggered_at':'2026-09-15T18:00:00+08:00',
            'signal_price':108,'signal_adjusted_price':108,'basis_id':'SYNTHETIC_CONSTANT_UNIT',
            'synthetic':True,'execution_model':model}


def test_raw_cn_adapter_roundtrip(tmp_path):
    o=order();q,r,s=evidence(tmp_path,o,'2026-09-16')
    assert q['reason'] is None and q['simulation_price']==108.5
    assert ex.validate_quote(q,o,'2026-09-16T09:30:10+08:00',s) is None
    assert Path(s[q['refs']['quote']]['path']).read_bytes().startswith(b'v_sz')


def test_self_made_normalized_json_cannot_replace_provider_bytes(tmp_path):
    o=order();q,r,s=evidence(tmp_path,o,'2026-09-16');q['simulation_price']=1
    assert ex.validate_quote(q,o,'2026-09-16T09:30:10+08:00',s)=='provider_derived_fields_mismatch'


@pytest.mark.parametrize('capture,success',[('09:30:59',True),('09:31:00',True),('09:31:01',False),('09:29:59',False)])
def test_strict_real_clock_window_boundaries(tmp_path,capture,success):
    q,_,_=evidence(tmp_path,order(),'2026-09-16',capture=capture)
    assert (q['reason'] is None)==success


def test_after_open_preflight_never_relabelled_before_open(tmp_path):
    q,_,_=evidence(tmp_path,order(),'2026-09-16',pre='09:30:04')
    assert 'preopen_eligibility_not_captured' in q['reason']


def test_hk_strict_unknown_is_not_cleared(tmp_path):
    q,_,_=evidence(tmp_path,order('HK'),'2026-09-16')
    assert 'HK_strict_preopen_venue_status_unavailable' in q['reason']


def test_hk_observed_separate_model_actual_receipt_price(tmp_path):
    o=order('HK','observed-quote-v1');q,_,s=evidence(tmp_path,o,'2026-09-16',model='observed-quote-v1')
    assert q['reason'] is None and q['simulation_price']==109
    assert q['simulation_at']=='2026-09-16T09:30:05+08:00'
    assert q['vcm_status']=='unknown_not_asserted'
    assert ex.validate_quote(q,order('HK'),'2026-09-16T09:30:10+08:00',s)=='execution_model_mismatch'


def test_weekend_live_source_cannot_be_monday_quote(tmp_path):
    q,_,_=evidence(tmp_path,order(),'2026-09-19')
    assert q['reason']=='market_closed_next_verified_session'


def test_same_bytes_distinct_capture_identity(tmp_path):
    a=record(tmp_path,'a',b'real-body-shape','2026-09-16T09:30:00+08:00')
    b=record(tmp_path,'b',b'real-body-shape','2026-09-16T09:30:01+08:00')
    s=mapping([a,b]);assert a['sha256']==b['sha256'] and a['source_id']!=b['source_id']
    assert s[a['source_id']]['fetched_at']!=s[b['source_id']]['fetched_at']


def test_provider_rate_error_is_not_empty_success(tmp_path):
    o=order();q,records,s=evidence(tmp_path,o,'2026-09-16');ref=q['refs']['suspensions'];m=s[ref]
    raw=json.dumps({'code':40203,'msg':'SYNTHETIC rate limit'}).encode();Path(m['path']).write_bytes(raw);m['sha256']=hashlib.sha256(raw).hexdigest()
    q=ex.derive(o,q['refs'],s,'strict-open-v1')
    assert 'provider_code_40203' in q['reason']


def test_network_deadline_keeps_error_metadata_no_fake_response(tmp_path):
    c=Collector(tmp_path,0);m=c.fetch('deadline','https://qt.gtimg.cn/q=sz001309')
    assert m['http_status'] is None and m['transport_error']=='TimeoutError'
    assert Path(m['path']).read_bytes()==b''


def test_expired_sell_same_signal_renews_and_exits_once(tmp_path):
    h=Run(tmp_path);p=fixture();h.run(p);p=append(p,108,200);h.run(p);p=append(p,109,opening=108.5);h.run(p,True)
    p=append(p,80);s=h.run(p);sell=next(o for o in s['ledger'].values() if o['side']=='SELL');sid=sell['signal_id'];original=sell['original_reasons']
    for _ in range(3):p=append(p,80);s=h.run(p)
    assert s['ledger'][sid]['execution_status']=='expired' and s['positions']
    p=append(p,79);s=h.run(p)
    assert s['ledger'][sid]['execution_status']=='pending' and len(s['ledger'][sid]['attempts'])==2
    p=append(p,78,opening=78.5);s=h.run(p,True)
    assert s['ledger'][sid]['execution_status']=='filled' and not s['positions'] and len(s['closed'])==1
    assert s['ledger'][sid]['original_reasons']==original and s['ledger'][sid]['attempts'][0]['status']=='expired'
    assert s['ledger'][sid]['fill']['attempt_number']==2
    assert observe(h.last_path,h.scope,h.root)['idempotent']
    assert len(snapshots(h.root)[-1][1]['closed'])==1


def test_explicit_cancel_expired_sell_never_autorenews(tmp_path):
    h=Run(tmp_path);p=fixture();h.run(p);p=append(p,108,200);h.run(p);p=append(p,109);h.run(p,True);p=append(p,80);h.run(p)
    for _ in range(3):p=append(p,80);s=h.run(p)
    sid=next(o['signal_id'] for o in s['ledger'].values() if o['side']=='SELL')
    cancel(h.root,h.scope,sid,'operator do not execute');p=append(p,79);s=h.run(p,True)
    assert s['ledger'][sid]['execution_status']=='cancelled' and s['positions']


def test_operator_pause_resume_is_explicit(tmp_path):
    h=Run(tmp_path);p=fixture();h.run(p);s=h.run(append(p,108,200));sid=next(iter(s['ledger']))
    pause(h.root,h.scope,sid,'operator pause');s=snapshots(h.root)[-1][1]
    assert s['ledger'][sid]['operator_paused']
    assert pause(h.root,h.scope,sid,'operator pause')['idempotent']
    pause(h.root,h.scope,sid,'operator resume',True)
    assert not snapshots(h.root)[-1][1]['ledger'][sid]['operator_paused']


def make_cn_run(tmp_path,model='strict-open-v1'):
    scope=tmp_path/'scope.json';write(scope,{'epoch':'SYNTHETIC-epoch','reset_at':'2026-09-12T00:00:00+08:00','holdings':[],
          'active_candidates':[{'code':'001309.SZ','market':'CN','admitted_at':'2026-09-12T01:00:00+08:00','research_event_id':'SYNTHETIC'}]})
    p=fixture();p['code']='001309.SZ';root=tmp_path/'archive'
    for i in range(2):
        if i:p=append(p,108,200)
        observe(bundle(p,tmp_path,i),scope,root,execution_model=model)
    return scope,root


def test_provider_bytes_to_consume_to_fill_not_only_json_adapter(tmp_path):
    scope,root=make_cn_run(tmp_path);s=snapshots(root)[-1][1];o=next(iter(s['ledger'].values()))
    q,records,_=evidence(tmp_path/'evidence',o,'2026-09-16')
    b={'source_kind':'SYNTHETIC_ONLY','scope_epoch':s['scope_epoch'],'scope_codes':[o['code']],
       'asof':'2026-09-16T09:30:10+08:00','execution_model':'strict-open-v1','inputs':records,
       'calendars':{'CN':q['refs']['calendar']},'execution_quotes':[q]}
    bp=tmp_path/'capture.json';write(bp,b);consume(bp,scope,root);s=snapshots(root)[-1][1]
    assert s['ledger'][o['signal_id']]['execution_status']=='filled' and s['positions']
    assert s['ledger'][o['signal_id']]['fill']['source_sha256']==q['source_sha256']
    assert consume(bp,scope,root)['idempotent']


def test_model_switch_existing_root_refused(tmp_path):
    scope,root=make_cn_run(tmp_path);p=fixture();p['code']='001309.SZ';p=append(p,109)
    with pytest.raises(ValueError,match='requires_new_isolated_root'):
        observe(bundle(p,tmp_path,5),scope,root,execution_model='observed-quote-v1')


@pytest.mark.parametrize('phase',['morning','evening','weekly'])
def test_all_report_consumers_fail_isolated(tmp_path,phase):
    from mt1.action_integration import cycle
    paths=[]
    for name in ('one','two','company'):
        p=tmp_path/(name+'.md');p.write_text('SYNTHETIC original '+name);paths.append(p)
    r=cycle(phase,tmp_path/'missing-root',tmp_path/'out',*paths)
    assert r['status']=='technical_failed_base_report_unblocked' and r['not_published']
    if phase!='weekly':
        text=Path(r['fallback']['path']).read_text()
        assert text.startswith('\n\n'.join(p.read_text() for p in paths))


def test_real_raw_protocol_fixture_readable():
    p=Path(__file__).resolve().parents[1]/'reports/mt13-r2-20260912/first-qt.raw'
    # A real weekend response only tests identity/field extraction, not trading.
    q=ex.qt(p.read_bytes(),'001309.SZ');h=ex.qt(p.read_bytes(),'00700.HK')
    assert q['code']=='001309.SZ' and h['code']=='00700.HK'
    assert 'limit_up' in q and 'limit_up' not in h


def test_quote_pair_requires_progress_not_two_hashes(tmp_path):
    o=order('HK','observed-quote-v1');q,_,s=evidence(tmp_path,o,'2026-09-16',model='observed-quote-v1')
    m=s[q['refs']['quote']];raw=quote_raw(o['code'],'2026/09/16 09:30:03',200)
    Path(m['path']).write_bytes(raw);m['sha256']=hashlib.sha256(raw).hexdigest()
    q=ex.derive(o,q['refs'],s,'observed-quote-v1')
    assert q['reason']=='no_new_reported_trade_retry_next_poll'


def test_hk_delayed_print_never_fills_backdated_open(tmp_path):
    o=order('HK','observed-quote-v1');q,_,s=evidence(tmp_path,o,'2026-09-16',model='observed-quote-v1',capture='09:45:05')
    m=s[q['refs']['previous_quote']];m['started_at']=m['fetched_at']='2026-09-16T09:45:02+08:00'
    q=ex.derive(o,q['refs'],s,'observed-quote-v1')
    assert q['reason'] is None and q['simulation_at']=='2026-09-16T09:45:05+08:00'
    assert q['simulation_price']==109 and q['quote_delay_seconds']==902


def test_renewal_forbids_same_day_earlier_open_backfill(tmp_path):
    from mt1.action_recovery import renew
    from mt1.action_loop import execute
    o=order();q,records,sources=evidence(tmp_path,o,'2026-09-16')
    o.update(side='SELL',execution_status='expired',position_key='v|001309.SZ',
             risk_reconfirmation={'active':True,'date':'2026-09-16','known_at':'2026-09-16T18:00:00+08:00'})
    state={'positions':{o['position_key']:{'entry_date':'2026-09-14'}},'transitions':[]}
    assert renew(state,o,'2026-09-16T18:00:00+08:00')
    execute(state,o,[q],'2026-09-16T18:00:00+08:00',sources,read(__import__('mt1.action_loop',fromlist=['POLICY']).POLICY))
    assert o['execution_status']=='blocked' and not o.get('fill')


def test_exit_only_confirmation_can_renew_expired_sell(tmp_path):
    h=Run(tmp_path);p=fixture();h.run(p);p=append(p,108,200);h.run(p);p=append(p,109);h.run(p,True);p=append(p,80);h.run(p)
    for _ in range(3):p=append(p,80);s=h.run(p)
    sid=next(o['signal_id'] for o in s['ledger'].values() if o['side']=='SELL')
    p=append(p,79);del p['bars'][-1]['vol'];s=h.run(p)
    assert s['cards'][0]['action']=='SELL' and not s['cards'][0]['hard_gaps']
    assert s['ledger'][sid]['execution_status']=='pending' and len(s['ledger'][sid]['attempts'])==2


@pytest.mark.parametrize('failure',['suspended','rate_limited'])
def test_raw_failure_three_sessions_expiry_renew_recover_single_exit(tmp_path,failure):
    scope,root=make_cn_run(tmp_path);p=fixture();p['code']='001309.SZ';p=append(p,108,200)
    def capture(day,o,label,fail=False):
        q,records,sources=evidence(tmp_path/label,o,day)
        if fail:
            m=sources[q['refs']['suspensions']]
            raw=json.dumps(response([{'ts_code':o['code'],'trade_date':day.replace('-',''),'suspend_type':'S'}]) if failure=='suspended' else {'code':40203,'msg':'SYNTHETIC rate limit'}).encode()
            Path(m['path']).write_bytes(raw);m['sha256']=hashlib.sha256(raw).hexdigest()
            q=ex.derive(o,q['refs'],sources,'strict-open-v1')
        asof=day+'T18:00:00+08:00' if fail else day+'T09:30:10+08:00'
        b={'source_kind':'SYNTHETIC_ONLY','scope_epoch':'SYNTHETIC-epoch','scope_codes':[o['code']],
           'asof':asof,'execution_model':'strict-open-v1','inputs':records,'calendars':{'CN':q['refs']['calendar']},'execution_quotes':[q]}
        path=tmp_path/(label+'.json');write(path,b);consume(path,scope,root)
        return path
    o=next(iter(snapshots(root)[-1][1]['ledger'].values()));capture('2026-09-16',o,'buy')
    p=append(p,109);observe(bundle(p,tmp_path,2),scope,root)
    p=append(p,80);observe(bundle(p,tmp_path,3),scope,root)
    o=next(o for o in snapshots(root)[-1][1]['ledger'].values() if o['side']=='SELL');sid=o['signal_id'];reason=o['original_reasons']
    for i,day in enumerate(['2026-09-18','2026-09-21','2026-09-22']):
        capture(day,o,'failure'+str(i),True);p=append(p,80)
    s=snapshots(root)[-1][1];assert s['ledger'][sid]['execution_status']=='expired' and s['positions']
    p=append(p,79);observe(bundle(p,tmp_path,4),scope,root)
    o=snapshots(root)[-1][1]['ledger'][sid];assert len(o['attempts'])==2
    path=capture('2026-09-24',o,'recovery');s=snapshots(root)[-1][1]
    assert not s['positions'] and len(s['closed'])==1 and s['ledger'][sid]['original_reasons']==reason
    assert s['ledger'][sid]['fill']['attempt_number']==2
    assert consume(path,scope,root)['idempotent']


def test_resume_never_fills_captured_price_before_resume(tmp_path):
    from mt1.action_loop import execute,POLICY
    o=order();q,records,sources=evidence(tmp_path,o,'2026-09-16')
    o.update(position_key='v|001309.SZ',execution_status='pending',execution_not_before='2026-09-16T10:00:00+08:00')
    state={'positions':{},'transitions':[]}
    execute(state,o,[q],'2026-09-16T10:00:00+08:00',sources,read(POLICY))
    assert o['execution_status']=='blocked' and not state['positions']


def test_observed_new_raw_print_to_separate_ledger(tmp_path):
    scope,root=make_cn_run(tmp_path,model='observed-quote-v1');o=next(iter(snapshots(root)[-1][1]['ledger'].values()))
    q,records,_=evidence(tmp_path/'evidence',o,'2026-09-16',model='observed-quote-v1')
    b={'source_kind':'SYNTHETIC_ONLY','scope_epoch':'SYNTHETIC-epoch','scope_codes':[o['code']],
       'asof':'2026-09-16T09:30:10+08:00','execution_model':'observed-quote-v1','inputs':records,
       'calendars':{'CN':q['refs']['calendar']},'execution_quotes':[q]}
    path=tmp_path/'capture.json';write(path,b);consume(path,scope,root);s=snapshots(root)[-1][1]
    fill=s['ledger'][o['signal_id']]['fill']
    assert fill['raw_price']==109 and fill['at']=='2026-09-16T09:30:05+08:00'
    assert fill['execution_model']=='observed-quote-v1'
    assert 'no actual executability' in fill['execution_assumption']
