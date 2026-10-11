"""Synthetic contract tests for production daily/plan consumers."""
import copy
import json
from datetime import timedelta
from unittest.mock import patch
import pytest
import test_research_pool_lifecycle as lifecycle
from mt1.research_pool import apply, snapshot, research_queues
from mt1.daily_tracking import track
from mt1.scope import load


@pytest.fixture
def case():
    c=lifecycle.ResearchPool(); c.setUp()
    try: yield c
    finally: c.doCleanups()


def test_daily_consumes_watch_and_exit_without_changing_holdings(case):
    case.put()
    fetch=lambda c:[{'date':'20261009','close':10}]
    gates={'CN':{'expected_date':'2026-10-09','allowed':False}}
    r=track(str(case.scope),now=case.now,fetch=fetch,gates=gates)
    assert len(r['rows'])==10
    row=next(r for r in r['rows'] if r['code']=='999999.SZ')
    assert row['holding_status']=='not_held' and row['research_pool_state']=='watch'
    case.put(request_id='exit',expected_version=1,state='exited',gap_or_exit_basis='synthetic invalidation')
    r=track(str(case.scope),now=case.now,fetch=fetch,gates=gates)
    row=next(r for r in r['rows'] if r['code']=='999999.SZ')
    assert row['research_pool_state']=='exited' and row['research_only']
    assert row['personal_pnl'] is None and row['recommendation_return'] is None
    assert case.scope.read_bytes()==case.before


def test_old_epoch_research_packet_never_passes(case,monkeypatch):
    monkeypatch.setenv('MT1_TRACKING_SCOPE',str(case.scope))
    from mt1.parallel import review_gate
    r=review_gate({'scope_epoch':'old'},'999999.SZ','TREND','20261009','evidence',decision_at=case.now)
    assert r['status']=='pending' and 'scope_epoch' in r['reasons'][0]


def test_scope_changed_during_source_archive_rolls_back(case):
    original=__import__('pathlib').Path.read_bytes
    hits=0
    def changed(path):
        nonlocal hits
        if path==case.scope:
            hits+=1
            if hits>=3: return b'changed'
        return original(path)
    with patch('pathlib.Path.read_bytes',changed), pytest.raises(ValueError,match='scope changed'):
        case.put()
    assert snapshot(case.scope,'synthetic-reset')['events']==[]


def test_new_work_does_not_readmit_active_or_exited(case):
    case.put()
    rows=[{'code':'999999.SZ','channel_risk':{'TREND':{'status':'unknown'}}},
          {'code':'999998.SZ','channel_risk':{'TREND':{'status':'unknown'}}}]
    assert [r['code'] for r in research_queues(rows,rows,load(case.scope))[1]]==['999998.SZ']
    case.put(request_id='exit',expected_version=1,state='exited',gap_or_exit_basis='synthetic exit')
    assert [r['code'] for r in research_queues(rows,rows,load(case.scope))[1]]==['999998.SZ']


def test_only_investment_and_outside_can_admit(case):
    for changes in ({'domain':'code'},{'code':'000000.SZ'}):
        with pytest.raises(ValueError): case.put(**changes)


def test_same_version_concurrent_writers_one_wins(case):
    from concurrent.futures import ThreadPoolExecutor
    # Initialize schema before racing independent admission writers.
    from mt1.store import Store
    from mt1.research_pool import db_path
    Store(db_path(case.scope)).close()
    def submit(key):
        try: return case.put(request_id=key)['request_id']
        except ValueError: return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as workers:
        result=list(workers.map(submit,['one','two']))
    assert result.count('conflict')==1
    assert len(snapshot(case.scope,'synthetic-reset')['events'])==1


def test_pipeline_resume_signature_ignores_projection_read_clock(case,monkeypatch):
    monkeypatch.setenv('MT1_TRACKING_SCOPE',str(case.scope))
    from mt1.pipeline import run
    first=run('sunday',case.root/'pipeline',case.root/'investment',case.now,{},run_id='same')
    second=run('sunday',case.root/'pipeline',case.root/'investment',case.now,{},run_id='same')
    assert first['report_path']!=second['report_path']
    assert second['research_pool']['counts']['watch']==0


def test_cron_patch_preserves_all_non_prompt_fields(tmp_path,monkeypatch):
    import os,subprocess,sys
    from scripts.deploy_research_pool import REMOTE,section
    root=tmp_path/'.homespace/cron';root.mkdir(parents=True)
    before={'name':'weekend-sunday-preview','domain':'investment','cron':'0 5 * * 0','delivery':'synthetic','enabled':True,'model':'keep','prompt':'USER CUSTOM\n最多10项，已移除风险否决且经过tracking-scope门禁'}
    p=root/'weekend-sunday-preview.json';p.write_text(json.dumps(before))
    cp=subprocess.run([sys.executable,'-c',REMOTE],input=json.dumps({before['name']:section('sunday')}),text=True,capture_output=True,env={**os.environ,'HOME':str(tmp_path)},check=True)
    after=json.loads(p.read_text())
    assert {k:v for k,v in before.items() if k!='prompt'}=={k:v for k,v in after.items() if k!='prompt'}
    assert after['prompt'].startswith('USER CUSTOM')
    assert 'research-event' in after['prompt'] and 'holdings-review.jsonl' in after['prompt']
    receipt=json.loads(cp.stdout)
    assert json.loads((__import__('pathlib').Path(receipt['backup'])/p.name).read_text())==before
    assert not receipt['decay_cron_exists']


def test_cron_patch_missing_does_not_create_schedule(tmp_path):
    import os,subprocess,sys
    from scripts.deploy_research_pool import REMOTE,section
    root=tmp_path/'.homespace/cron';root.mkdir(parents=True)
    cp=subprocess.run([sys.executable,'-c',REMOTE],input=json.dumps({'missing':section('sunday')}),text=True,capture_output=True,env={**os.environ,'HOME':str(tmp_path)})
    assert cp.returncode!=0 and list(root.iterdir())==[]


def test_data_blocked_does_not_reset_original_thesis_on_resume(case):
    first=case.put()
    case.put(state='pending',request_id='blocked',expected_version=1,gap_or_exit_basis='data unavailable',research_outcome='data_blocked')
    resumed=case.put(request_id='resumed',expected_version=2,reason='new review wording')
    assert resumed['original']==first['original']
    assert resumed['episode_admitted_at']==first['episode_admitted_at']
    with pytest.raises(ValueError,match='explicit exited'):
        case.put(state='rejected',request_id='bad-exit',expected_version=3,reason='thesis failed',gap_or_exit_basis='failure',research_outcome='completed_not_qualified')
