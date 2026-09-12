"""SYNTHETIC clock/network/storage injection. Actual worker loop + adapter + execute."""
from datetime import datetime,timedelta
import pytest
from mt1 import action_worker as w
from mt1.action_loop import execute,read,POLICY
from test_mt13_execution_worker import order,evidence,record,quote_raw

@pytest.mark.parametrize('failure',[None,'stale','unchanged','rate_limit'])
def test_hk_twenty_minute_delay_worker_from_0924(tmp_path,monkeypatch,failure):
    start=datetime.fromisoformat('2026-09-16T09:24:00+08:00');clock=[0.0]
    monkeypatch.setattr(w,'now',lambda:(start+timedelta(seconds=clock[0])).isoformat())
    monkeypatch.setattr(w.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(w.time,'sleep',lambda n:clock.__setitem__(0,clock[0]+n))
    o=order('HK','observed-quote-v1');o.update(position_key='v|00700.HK',version='v',scope_epoch='SYNTHETIC-epoch',execution_status='pending')
    state={'synthetic':False,'execution_model':'observed-quote-v1','ledger':{o['signal_id']:o},'positions':{},'transitions':[]}
    scope={'epoch':'SYNTHETIC-epoch','holdings':{},'recommendations':{},'candidates':{'00700.HK':{'market':'HK'}}}
    monkeypatch.setattr(w,'load',lambda p:scope)
    monkeypatch.setattr(w,'snapshots',lambda p:[('SYNTHETIC_IN_MEMORY',state)])
    monkeypatch.setattr(w,'latest_panels',lambda p:{})
    monkeypatch.setattr(w,'anchors_for',lambda *a:{})
    _,rs,_=evidence(tmp_path/'pre',o,'2026-09-16',model='observed-quote-v1',pre='09:24:00')
    pre={'calendar_HK':rs[0],'factor_00700.HK':rs[1]}
    for name,m in pre.items():m.update(name=name,duration_seconds=0,transport_error=None)
    monkeypatch.setattr(w,'preflight',lambda *a:pre)
    def fetch(self,name,url,*args):
        assert url.endswith('q=hk00700')
        at=start+timedelta(seconds=clock[0]);provider=at-timedelta(seconds=1200 if failure!='stale' else 1201)
        if failure=='unchanged':provider=start.replace(hour=9,minute=30)
        raw=quote_raw('00700.HK',provider.strftime('%Y/%m/%d %H:%M:%S'),vol=200+int(clock[0]))
        m=record(self.out,name,raw,at.isoformat(),url=url)
        m.update(name=name,duration_seconds=0,transport_error=None)
        if failure=='rate_limit':m['http_status']=429
        return m
    monkeypatch.setattr(w.Collector,'fetch',fetch)
    # Storage boundary is injected, but simulation engine validates raw bytes again.
    def consume(path,*args):
        b=read(path);assert len(b['inputs'])<=4
        execute(state,o,b['execution_quotes'],b['asof'],w.mapping(b['inputs']),read(POLICY))
        return {'SYNTHETIC_ONLY':True,'status':o['execution_status']}
    monkeypatch.setattr(w,'consume',consume)
    result=w.run_worker('SYNTHETIC',tmp_path/'root',tmp_path/'out','watch',2700,30,market='HK')
    assert clock[0]==2460 and result['status']=='bounded_window_complete_retry_next_session'
    assert result['window_end'].endswith('10:05:00+08:00')
    if failure is None:
        assert o['execution_status']=='filled'
        assert o['fill']['at']=='2026-09-16T09:50:30+08:00'
        assert o['fill']['provider_at']=='2026-09-16T09:30:30+08:00'
        assert len(state['positions'])==1
    else:assert not state['positions'] and 'fill' not in o

@pytest.mark.parametrize('model,market,end',[('strict-open-v1','CN','09:31:01'),('strict-open-v1','HK','09:31:01'),('observed-quote-v1','CN','09:40:00'),('observed-quote-v1','HK','10:05:00')])
def test_frozen_market_capture_end(model,market,end):
    assert w.WINDOWS[model][market]==end


def test_late_worker_exits_without_http(tmp_path,monkeypatch):
    monkeypatch.setattr(w,'now',lambda:'2026-09-16T10:06:00+08:00')
    monkeypatch.setattr(w,'load',lambda p:{'holdings':{'00700.HK':{'market':'HK'}},'candidates':{},'recommendations':{}})
    monkeypatch.setattr(w,'latest_panels',lambda p:{})
    monkeypatch.setattr(w,'anchors_for',lambda *a:{})
    monkeypatch.setattr(w,'snapshots',lambda p:[('SYNTHETIC',{'synthetic':False,'execution_model':'observed-quote-v1'})])
    monkeypatch.setattr(w,'preflight',lambda *a:pytest.fail('expired window must not fetch'))
    r=w.run_worker('SYNTHETIC',tmp_path/'root',tmp_path/'out','watch',2460,3,market='HK')
    assert r['status']=='capture_window_finished_retry_next_session' and r['receipts']==[]


def test_natural_audit_readonly_old_real_capture():
    from mt1.action_integration import verify_run
    from pathlib import Path
    base=Path('reports/mt13-r2-20260912')
    r=verify_run(base/'real-consumer-archive',base/'real-once',[base/'structural-morning',base/'not-created'])
    assert r['verified_raw_count']==14 and r['validated'] is False
    assert not r['positions'] and not r['signals']
    assert r['reports'][1]['missing'] and not r['reports'][0]['missing']
