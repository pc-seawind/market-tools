import json
import os
from pathlib import Path
import subprocess
import time
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
import recap_runtime as rt
import recap_rpc as rpc


def pick(day='20260924', **extra):
    return {'evaluations':[{'stock':{'code':'A','trade_date':day}}],
            'sector_score':{'data_quality':'proxy','news_note':'stub-9'}, **extra}


def test_partial_consumes_valid_independent_sectors():
    value={'meta':{'fresh':True,'trade_date':'20260924','errors':['B timeout'], 'n_picks_selected':3},
           'scores':[{'concept':'A','data_quality':'proxy'}],
           'picks':{'A':pick(),'B':{'error':'timeout'},'C':pick('20260925')}}
    consumed=rt.view(value,'2026-09-24')
    assert list(consumed['picks'])==['A']
    assert set(consumed['coverage']['failed'])=={'B','C'}
    assert consumed['coverage']['complete_facts']==0
    assert consumed['picks']['A']['source_grade']=='degraded_proxy_neutral_stub'
    assert 'B' in value['picks']  # untouched raw archive


def test_incomplete_internal_rpc_not_success():
    v={'meta':{'fresh':True,'trade_date':'20260924'},'picks':{'A':pick(coverage={'missing':['A']})}}
    assert not rt.view(v,'2026-09-24')['picks']
    v['picks']['A']=pick();v['meta']['fresh']=False
    assert not rt.view(v,'2026-09-24')['picks']


def test_reuse_rejects_future_expired_changed_input():
    now=datetime.now()
    v={'meta':{'input_hash':'hash','date':now.date().isoformat(),'fresh':True,'generated_at':now.isoformat()}}
    assert rt.reusable(v,'hash',now.date().isoformat(),now)
    assert not rt.reusable(v,'other',now.date().isoformat(),now)
    for delta in (timedelta(seconds=1),-timedelta(hours=7)):
        v['meta']['generated_at']=(now+delta).isoformat()
        assert not rt.reusable(v,'hash',now.date().isoformat(),now)


def test_real_process_group_timeout(tmp_path):
    marker=tmp_path/'leaked'
    child=f'import time; from pathlib import Path; time.sleep(1); Path({str(marker)!r}).touch()'
    parent=f'import subprocess,sys,time; subprocess.Popen([sys.executable,"-c",{child!r}], start_new_session=True); time.sleep(10)'
    import pytest
    with pytest.raises(subprocess.TimeoutExpired):
        rt.run(['python3','-c',parent],timeout=.15,text=True)
    time.sleep(1.1)
    assert not marker.exists()


def test_rpc_cache_limit_and_failed_not_cached(tmp_path, monkeypatch):
    import threading
    monkeypatch.setenv('RECAP_RPC_CACHE',str(tmp_path))
    starts=[]; active=0; maximum=0; lock=threading.Lock()
    def provider(args, **kw):
        nonlocal active,maximum
        with lock: starts.append(time.monotonic());active+=1;maximum=max(maximum,active)
        time.sleep(.03)
        with lock:active-=1
        return subprocess.CompletedProcess(args,0,'trade_date,close\n20260924,1\n','')
    monkeypatch.setattr(rpc,'run',provider)
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows=list(pool.map(lambda i:rpc.csv_rpc([str(i)],'daily',{'ts_code':str(i)}),range(4)))
    assert all(rows) and maximum<=2
    assert all(b-a>=.45 for a,b in zip(starts,starts[1:]))
    rpc.csv_rpc(['0'],'daily',{'ts_code':'0'})
    assert len(starts)==4
    # Future/expired entries never serve as cache hits.
    for p in tmp_path.glob('*.json'):
        data=json.loads(p.read_text());data['fetched_at']=time.time()+60;rt.atomic(p,data)
    rpc.csv_rpc(['0'],'daily',{'ts_code':'0'});assert len(starts)==5
    monkeypatch.setattr(rpc,'run',lambda args,**kw:subprocess.CompletedProcess(args,1,'','failed'))
    assert rpc.csv_rpc(['bad'],'daily',{})==[]
    assert len(list(tmp_path.glob('*.json')))==4


def test_same_day_partial_recovery_and_idempotency(tmp_path,monkeypatch):
    import sys
    script=Path(__file__).resolve().parents[1]/'evening_recap_data.sh'
    body=script.read_text().split("<<'PY'\n",1)[1].split('\nPY\n',1)[0]
    scores=tmp_path/'scores.json';out=tmp_path/'out.json'
    scores.write_text(json.dumps([{'concept':n,'tier1_pass':True,'total_score':60} for n in ['A','B']]))
    monkeypatch.setenv('EVENING_RECAP_REMAINING_SECONDS','120')
    monkeypatch.setattr(sys,'argv',['-',str(scores),str(out),'2','2026-09-24'])
    attempts=[];fail=True
    def provider(args,**kw):
        nonlocal fail
        if 'sector_picks.py' in args:
            concept=args[args.index('--sector')+1];attempts.append(concept)
            if concept=='B' and fail: raise subprocess.TimeoutExpired(args,1)
            return subprocess.CompletedProcess(args,0,json.dumps(pick()),'')
        return subprocess.CompletedProcess(args,0,'trade_date\n20260924\n' if 'index_daily' in args else 'cal_date,is_open\n20260924,1\n','')
    monkeypatch.setattr(subprocess,'run',provider);monkeypatch.setattr(rt,'run',provider)
    def execute():
        exec(compile(body,str(script),'exec'),{'__name__':'__main__'})
        return json.loads(out.read_text())
    first=execute();assert first['meta']['status']=='partial'
    fail=False
    second=execute();assert attempts==['A','B','B']
    assert second['meta']['reused']==['A'] and second['meta']['status']=='complete'
    assert second['coverage']['status']=='complete'
    execute();assert attempts==['A','B','B']
    assert list(Path(str(out)+'.history').glob('*.json'))
    # Changed inputs invalidate all sector successes; failed items stop at two.
    scores.write_text(scores.read_text().replace('60', '61'))
    attempts.clear(); fail=True
    execute();execute();last=execute()
    assert attempts==['A','B','B']
    assert last['meta']['attempt_counts']['B']==2
    assert any('retry limit' in e for e in last['meta']['errors'])


def test_pipeline_refreshes_partial_even_if_stage_done(tmp_path):
    from tests.test_mt1 import calendar
    from mt1.pipeline import run
    now=datetime.fromisoformat('2026-09-24T19:00:00+08:00')
    value={'meta':{'fresh':True,'trade_date':'20260924','errors':['B timeout']},
           'scores':[], 'picks':{'A':pick(),'B':{'error':'timeout'}}}
    fixture={'calendars':{'CN':calendar(now)},'recap':value}
    first=run('evening',tmp_path/'state',tmp_path/'investment',now,fixture)
    assert list(first['raw_recap']['picks'])==['A']
    # Simulate externally repaired raw checkpoint without changing fixture signature.
    path=tmp_path/'state/runs/2026-09-24-evening/recap.json'
    cached=json.loads(path.read_text());cached['picks']={};rt.atomic(path,cached)
    second=run('evening',tmp_path/'state',tmp_path/'investment',now,fixture)
    assert list(second['raw_recap']['picks'])==['A']
    assert second['raw_recap']['coverage']['failed']['B']['reason']=='timeout'


def test_rpc_future_payload_never_cached(tmp_path,monkeypatch):
    monkeypatch.setenv('RECAP_RPC_CACHE',str(tmp_path))
    monkeypatch.setattr(rpc,'run',lambda args,**kw:subprocess.CompletedProcess(args,0,'trade_date,close\n20991231,1\n',''))
    assert rpc.csv_rpc(['future'],'daily',{})==[]
    assert not list(tmp_path.glob('*.json'))
