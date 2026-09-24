"""Offline provider substitution, real default CLI -> pipeline -> shell -> recovery."""
import copy
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
from datetime import datetime, timezone
import pytest
import mt1.pipeline as pipeline
import mt1.recap_collection as collection
from mt1.finalize import finalize
from mt1.store import Store
from tests.test_mt1 import calendar
from recap_runtime import atomic, view

ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT/'tests/fixtures/recap-followup/historical-bundle.json'


def test_default_entry(tmp_path, monkeypatch, fail_forever=False):
    """ONE main() call. No human re-start, real collector process twice."""
    from mt1 import sweep
    provider = tmp_path/'provider'; provider.mkdir()
    shutil.copyfile(ROOT/'evening_recap_data.sh', provider/'evening_recap_data.sh')
    (provider/'evening_recap_data.sh').chmod(0o755)
    day = datetime.now().strftime('%Y%m%d')
    (provider/'sector_score.py').write_text("import json; print(json.dumps([{'concept':n,'tier1_pass':True,'total_score':60} for n in ['A','B']]))")
    (provider/'tushare.py').write_text(f"import sys; print('trade_date\\n{day}\\n' if 'index_daily' in sys.argv else 'cal_date,is_open\\n{day},1\\n')")
    (provider/'sector_picks.py').write_text('''import sys,json
from pathlib import Path
name=sys.argv[sys.argv.index('--sector')+1]
p=Path('calls.json');calls=json.loads(p.read_text()) if p.exists() else []
calls.append(name);p.write_text(json.dumps(calls))
if name=='B' and (calls.count('B')==1 or FAIL_FOREVER):sys.exit(1)
print(json.dumps({'evaluations':[{'stock':{'code':name,'trade_date':DAY}}], 'sector_score':{'data_quality':'proxy'}}))
'''.replace('DAY',repr(day)).replace('FAIL_FOREVER',repr(fail_forever)))
    # CLI writes this environment key; register it so fixture teardown restores it.
    monkeypatch.setenv('MT1_TRACKING_SCOPE',os.environ.get('MT1_TRACKING_SCOPE','/home/emox/work/investment/reference/tracking-scope.json'))
    monkeypatch.setenv('PYTHONPATH',str(ROOT))
    monkeypatch.setenv('EVENING_RECAP_BUDGET_SECONDS','20')
    monkeypatch.setenv('EVENING_RECAP_REVERSAL_QUOTA','0')
    monkeypatch.setattr(pipeline,'HERE',provider)
    monkeypatch.setattr(collection,'HERE',provider)
    monkeypatch.setattr(pipeline,'Path',lambda v: tmp_path/'raw' if str(v)=='/tmp' else Path(v))
    (tmp_path/'raw').mkdir()
    # Synthetic exchange/provider fixtures, NOT evidence of a natural trading day.
    monkeypatch.setattr(pipeline,'cn_calendar',lambda now:calendar(now))
    monkeypatch.setattr(pipeline,'foreign_calendar',lambda now,market:calendar(now,market))
    monkeypatch.setattr(sweep,'launch',lambda *a:{'test_only':'no sweep launched'})
    monkeypatch.setattr(sweep,'snapshot',lambda *a:{})
    now=datetime.now().astimezone().replace(hour=20,minute=0,second=0,microsecond=0)
    # Fixed weekday calendar for this offline scenario; no production gate patched.
    def synthetic_calendar(now,market='CN'):
        c=calendar(now,market)
        for row in c['days']:
            if row['date']==now.date().isoformat():row['is_open']=True
        return c
    monkeypatch.setattr(pipeline,'cn_calendar',synthetic_calendar)
    monkeypatch.setattr(sys,'argv',['mt1.py','--state-dir',str(tmp_path/'state'),
        'run','evening','--collect','--investment-dir',str(tmp_path/'investment'),
        '--now',now.isoformat(),'--run-id','default-entry-offline'])
    runpy.run_path(str(ROOT/'mt1.py'),run_name='__main__')
    result=json.loads((tmp_path/'state/runs/default-entry-offline/report.json').read_text())
    recovery=result['raw_recap']['recovery']
    assert recovery['status']==('attempt_limit' if fail_forever else 'complete')
    assert len(recovery['attempts'])==2
    assert recovery['elapsed_seconds']<20
    assert recovery['attempts'][1]['collector_budget_seconds']<=recovery['attempts'][0]['collector_budget_seconds']
    assert json.loads((provider/'calls.json').read_text())==['A','B','B']
    assert set(result['raw_recap']['picks'])==({'A'} if fail_forever else {'A','B'})
    assert result['raw_recap']['meta']['reused']==['A']
    assert result['raw_recap']['meta']['attempt_counts']=={'A':1,'B':2}
    assert result['raw_recap']['coverage']['status']==('partial' if fail_forever else 'complete')
    if fail_forever:
        failure=result['raw_recap']['coverage']['failed']['B']
        assert failure['state']=='attempt_limit' and not failure['automatic_retry_pending']
        assert 'no automatic retry' in failure['next_action']
        assert result['raw_recap']['meta']['sector_attempts']['B']['state']=='attempt_limit'


def test_default_entry_terminal(tmp_path, monkeypatch):
    test_default_entry(tmp_path, monkeypatch, fail_forever=True)


def test_shared_budget_terminal_and_limit(tmp_path,monkeypatch):
    path=tmp_path/'raw.json'; receipt=tmp_path/'receipt.json'
    v={'meta':{'date':'2026-09-24','fresh':True,'trade_date':'20260924','input_hash':'same',
                'attempt_counts':{'B':1},'errors':['failed']},'picks':{'B':{'error':'failed'}}}
    clock=[0.0];calls=[]
    monkeypatch.setenv('EVENING_RECAP_BUDGET_SECONDS','10')
    monkeypatch.setattr(collection.time,'monotonic',lambda:clock[0])
    def provider(args,**kw):
        calls.append(kw);atomic(path,v);clock[0]+=10
        return subprocess.CompletedProcess(args,0,'','')
    monkeypatch.setattr(collection,'run',provider)
    value,r=collection.collect(path,'2026-09-24','2026-09-24',receipt)
    assert len(calls)==1 and r['status']=='budget_exhausted'
    c=view({**value,'recovery':r},'2026-09-24')['coverage']['failed']['B']
    assert c['state']=='budget_exhausted' and c['automatic_retry_pending'] is False
    assert c['inspect_command'][-1]==str(path)+'.rpc.jsonl'
    v['meta']['attempt_counts']['B']=2;atomic(path,v)
    _,r=collection.collect(path,'2026-09-24','2026-09-24',receipt)
    assert r['status']=='attempt_limit' and len(calls)==1
    assert r['automatic_retry_pending'] is False
    with pytest.raises(ValueError,match='historical'):
        collection.collect(path,'2026-09-23','2026-09-24',receipt)


def test_history_wrong_level_rejected_before_writes(tmp_path):
    bundle=json.loads(HISTORY.read_text())
    for top in (False,True):
        wrong=copy.deepcopy(bundle)
        if top:wrong['review_items']=[]
        with pytest.raises(ValueError,match='review_items_wrong_level') as exc:
            finalize(wrong,tmp_path/'state',tmp_path/'investment')
        assert 'expected_version' in str(exc.value) and 'bundle.review_items' in str(exc.value)
        assert not (tmp_path/'state').exists()
        assert not (tmp_path/'investment').exists()


def test_historical_pending_visible_without_research_signoff(tmp_path):
    bundle=copy.deepcopy(json.loads(HISTORY.read_text()))
    historical=json.loads((ROOT/'tests/fixtures/recap-followup/historical-run.json').read_text())
    by_code={p['code']:p for p in historical['plans']}
    store=Store(tmp_path/'state/plans.db')
    # Replay original immutable plan snapshots into an isolated test ledger only.
    for p in historical['plans']:
        store.apply('plan:'+p['plan_id'],0,'replay:'+p['plan_id'],p,'isolated historical replay',lambda old,v:v.copy())
    before=store.all()
    versions={p['plan_id']:p['version'] for p in before}
    bundle['review_items']=[{'plan_id':by_code[r['code']]['plan_id'],
         'expected_version':versions[by_code[r['code']]['plan_id']], 'status':'pending',
         'note':r['reason']} for r in bundle['research'].pop('review_items')]
    atomic(tmp_path/'state/runs/2026-09-24-morning/report.json',historical)
    atomic(tmp_path/'corrected-bundle.json',bundle)
    result=finalize(bundle,tmp_path/'state',tmp_path/'investment')
    assert len(result['pending_review_ids'])==9
    assert result['reviewed_plan_ids']==[] and len(result['unreviewed_plan_ids'])==9
    assert result['research_status']=='not_verified' and result['changes']==[]
    assert store.all()==before
    text=Path(result['report_path']).read_text()
    assert '覆盖 0/9' in text and text.count('待复核：')==9
    assert result['errors']==[]
    atomic(tmp_path/'consumer-receipt.json',result)
    store.close()
