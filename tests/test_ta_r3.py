"""Synthetic failure/dependency/workflow boundaries, not new financial samples."""
from copy import deepcopy
from pathlib import Path
import json
import pytest
from mt1 import ta_pipeline as p
from mt1.ta_research import save,read,digest
from test_ta_r2 import fixture_report


def setup_launch(tmp_path,monkeypatch):
    report,scope,catalog=fixture_report(tmp_path,monkeypatch);root=tmp_path/'root';clock=['2026-09-15T10:00:00+00:00'];calls=[]
    monkeypatch.setattr(p,'now',lambda:clock[0])
    class R:returncode=0
    monkeypatch.setattr(p.subprocess,'run',lambda *a,**kw:(calls.append(a) or R()))
    launch=lambda **kw:p.request_refresh(report,'evening',root,scope,catalog,**kw)
    return root,clock,calls,launch


def test_launch_failed_backoff_then_success(tmp_path,monkeypatch):
    root,clock,calls,launch=setup_launch(tmp_path,monkeypatch)
    class R:
        def __init__(self,n):self.returncode=n
    monkeypatch.setattr(p.subprocess,'run',lambda *a,**kw:(calls.append(a) or R(1 if len(calls)==1 else 0)))
    a=launch();assert a['status']=='launch_failed'
    assert launch()['recovery']=='backoff' and len(calls)==1
    clock[0]='2026-09-15T10:01:01+00:00';b=launch();assert b['status']=='launched' and b['attempt']==2 and len(calls)==2
    assert launch()['reused_request'] and len(calls)==2
    assert read(root/'requests'/a['request_id']/'history/launch-1.json')['launch']['returncode']==1


@pytest.mark.parametrize('worker_state',['failed','running','launching'])
def test_exited_worker_or_timeout_can_resume(tmp_path,monkeypatch,worker_state):
    root,clock,calls,launch=setup_launch(tmp_path,monkeypatch);a=launch();q=root/'requests'/a['request_id'];p.atomic_json(q/'status.json',{'status':worker_state,'started_at':clock[0]})
    monkeypatch.setattr(p,'unit_state',lambda _: 'failed');clock[0]='2026-09-15T11:02:00+00:00'
    assert launch()['attempt']==2 and len(calls)==2


@pytest.mark.parametrize('state',['active','activating','deactivating','unknown'])
def test_no_duplicate_when_running_or_supervision_unknown(tmp_path,monkeypatch,state):
    root,clock,calls,launch=setup_launch(tmp_path,monkeypatch);launch();clock[0]='2026-09-15T11:02:00+00:00';monkeypatch.setattr(p,'unit_state',lambda _:state)
    assert launch()['recovery']=='wait_for_confirmed_exit' and len(calls)==1


def test_completed_and_exhausted_budget(tmp_path,monkeypatch):
    root,clock,calls,launch=setup_launch(tmp_path,monkeypatch);a=launch();q=root/'requests'/a['request_id'];p.atomic_json(q/'status.json',{'status':'completed_pending_analyst_review'})
    clock[0]='2026-09-15T11:00:00+00:00';assert launch()['status']=='completed' and len(calls)==1
    p.atomic_json(q/'status.json',{'status':'failed'});old=read(q/'launch.json');old.update(attempt=3,status='launch_failed');p.atomic_json(q/'launch.json',old)
    assert launch()['recovery']=='attempt_limit' and len(calls)==1
    assert launch(recover_reason='SYNTHETIC repaired bus')['attempt']==4 and len(calls)==2


def test_uncertain_launch_timeout_requires_supervision(tmp_path,monkeypatch):
    root,clock,calls,launch=setup_launch(tmp_path,monkeypatch)
    def fail(*a,**kw):calls.append(a);raise TimeoutError('SYNTHETIC')
    monkeypatch.setattr(p.subprocess,'run',fail);assert launch()['status']=='refresh_failed'
    clock[0]='2026-09-15T11:00:00+00:00';monkeypatch.setattr(p,'unit_state',lambda _:'unknown')
    assert launch()['recovery']=='wait_for_confirmed_exit' and len(calls)==1


def test_dependency_hashes_schema_only_equivalence():
    from mt1.ta_dependencies import check
    a={'facts':[{'text':'profit declined','evidence_ids':['e']}]};b=deepcopy(a);b['facts'][0]['numbers']=[]
    errors,record=check('bull_cross',{'initial_arguments':{'bull':a,'bear':a}},{'bull':b,'bear':b})
    assert errors==[] and record['bear']['request_argument_hash']!=record['bear']['current_output_hash']
    b['facts'][0]['text']='profit increased'
    assert check('bull_cross',{'initial_arguments':{'bull':a,'bear':a}},{'bull':a,'bear':b})[0]==['stale_dependency:bear']
    assert check('C',{'debate':{}},{})[0]


def test_material_initial_change_regenerates_both_cross_and_C(tmp_path,monkeypatch):
    from mt1 import ta_revision as rev,ta_review
    from mt1.ta_research import ROLES,evidence
    from mt1.ta_dependencies import DEPS,check
    from mt1.timing_cli import file_hash
    from test_ta_research import output
    parent=tmp_path/'parent';parent.mkdir();raw=tmp_path/'raw';raw.write_text('SYNTHETIC')
    e=evidence('001309.SZ','financial',{'roe':1},'https://test.invalid','2026-09-15T10:00:00+08:00','2026-09-15T11:00:00+08:00',raw)
    good=output(e);good['short_term'].update(trigger='未知',invalidation='未知');stock={'code':'001309.SZ','name':'SYNTHETIC','evidence':[e]};frozen={'stocks':[stock],'as_of':'2026-09-15T20:00:00+08:00'};save(parent/'input.json',frozen)
    def files(d,r,payload,o):
        save(d/(r+'.request.json'),{'messages':[{'content':json.dumps(payload)}]});save(d/(r+'.response.json'),{'SYNTHETIC':o});c={'output':o,'errors':[]};save(d/(r+'.json'),c);return c
    calls={}
    for r in ROLES:
        payload={'role':r,'evidence':[e]}
        if r in DEPS:payload['debate' if r=='C' else 'initial_arguments']={x:good for x in DEPS[r]}
        calls[r]=files(parent/stock['code'],r,payload,good)
    original={'run_id':'SYNTHETIC','stocks':[{**stock,'calls':calls}]};save(parent/'results.json',original);save(parent/stock['code']/'result.json',original['stocks'][0])
    mp=parent/'manifest.json';save(mp,{'run_id':'SYNTHETIC','input_hash':digest(frozen),'files':[{'path':str(x.relative_to(parent)),'sha256':file_hash(x)} for x in parent.rglob('*') if x.is_file()]})
    fp=tmp_path/'feedback';save(fp,{'manifest_hash':file_hash(mp),'stocks':{'001309.SZ':{'bear':['SYNTHETIC change']}}});invoked=[];new_outputs={r:good for r in ROLES}
    def call(d,r,payload,es):
        invoked.append(r)
        if r in DEPS:assert not check(r,payload,new_outputs)[0]
        o=deepcopy(good)
        if r=='bear':o['facts'][0]['text']='SYNTHETIC actual argument changed'
        new_outputs[r]=o;return files(d,r,payload,o)
    monkeypatch.setattr(rev,'call_model',call);monkeypatch.setattr(ta_review,'register',lambda *a:None)
    rev.revise(mp,fp,tmp_path/'new');assert invoked==['bear','bull_cross','bear_cross','C']


def workflow_fixture(tmp_path,monkeypatch):
    from mt1 import ta_workflow as w
    from mt1.timing_cli import file_hash
    d=tmp_path/'run';d.mkdir();stock={'code':'001309.SZ','market':'CN','target_session':'2026-09-16','evidence':[]};save(d/'input.json',{'as_of':'2026-09-15T20:00:00+08:00','stocks':[stock]})
    save(d/'manifest.json',{'run_id':'SYNTHETIC','files':[{'path':'input.json','sha256':file_hash(d/'input.json')}]});mh=file_hash(d/'manifest.json');root=tmp_path/'root';save(root/'latest.json',{'manifest':str(d/'manifest.json'),'sha256':mh,'run_id':'SYNTHETIC'})
    monkeypatch.setattr(w,'now',lambda:'2026-09-15T21:00:00+08:00')
    item={'kind':'followup','manifest_hash':mh,'code':'001309.SZ','post_evidence':[], 'source_work':{'status':'blocked','rationale':'SYNTHETIC wait new original'},'short_term':{'status':'not_matured','rationale':'not closed','evidence_ids':[]},'proposition_checks':[{'status':'not_matured','reviewer':'SYNTHETIC','rationale':'next quarter','evidence_ids':[]}],'error_categories':{k:{'status':'unknown','rationale':'no new evidence'} for k in w.CATEGORIES}}
    batch={'owner':w.OWNER,'reviewer':'SYNTHETIC','reviewed_at':'2026-09-15T21:00:00+08:00','next_check_at':'2026-09-16T21:00:00+08:00','items':[item]};return w,root,batch


def test_writeback_idempotent_owner_nextcheck_and_inbox(tmp_path,monkeypatch):
    w,root,b=workflow_fixture(tmp_path,monkeypatch);f=tmp_path/'batch';save(f,b);a=w.apply(f,root);assert not a['records'][0]['reused']
    assert w.apply(f,root)['records'][0]['reused']
    r=w.inbox(root,b['reviewed_at'])['items'][0];assert r['followup_status']=='recorded' and not r['due_now'] and r['next_check_at']==b['next_check_at']


@pytest.mark.parametrize('fault',['owner','clock','mature','business','errors'])
def test_invalid_review_rejected_without_partial_write(tmp_path,monkeypatch,fault):
    w,root,b=workflow_fixture(tmp_path,monkeypatch);i=b['items'][0]
    if fault=='owner':b['owner']='nobody'
    if fault=='clock':b['next_check_at']=b['reviewed_at']
    if fault=='mature':i['short_term']['status']='confirmed'
    if fault=='business':i['proposition_checks'][0]['status']='confirmed'
    if fault=='errors':i['error_categories']['logic']={'status':'identified','rationale':'price fell'}
    f=tmp_path/'batch';save(f,b)
    with pytest.raises(ValueError):w.apply(f,root)
    assert not list((root/'workflow/followups').rglob('*.json'))


def test_recomputed_quality_retains_immutable_old_snapshot(tmp_path,monkeypatch):
    from mt1 import ta_quality
    root=tmp_path/'root';req=root/'requests/key';ident={'source':'SYNTHETIC'}
    save(req/'input.json',{'identity':{'report':'report'},'scope':'scope','catalog':'cat'})
    monkeypatch.setattr(p,'identity',lambda *a:ident);monkeypatch.setattr(p,'digest',lambda _: 'key')
    mp=tmp_path/'run/manifest.json';save(mp,{'SYNTHETIC':True});old=mp.parent/'automatic-quality.json';save(old,{'old':True})
    from mt1 import ta_evidence
    monkeypatch.setattr(ta_evidence,'collect',lambda *a,**kw: {})
    monkeypatch.setattr(p,'run',lambda *a,**kw:{'manifest':str(mp),'run_id':'SYNTHETIC'})
    monkeypatch.setattr(ta_quality,'automatic',lambda *a:{'new':True});monkeypatch.setattr(p,'due_queue',lambda *a:{'count':0})
    assert p.worker(root,'key')['status']=='completed_pending_analyst_review'
    assert read(old)=={'old':True} and read(root/'quality-history/key.json')=={'new':True}


def test_business_confirmation_cannot_use_price_evidence(tmp_path,monkeypatch):
    from mt1.ta_research import evidence
    w,root,b=workflow_fixture(tmp_path,monkeypatch);raw=tmp_path/'quote';raw.write_text('SYNTHETIC')
    e=evidence('001309.SZ','quote',{'code':'001309.SZ','last':100,'low':99,'high':101,'provider_at':'2026-09-15T20:30:00+08:00'},'https://test.invalid','2026-09-15T20:30:00+08:00','2026-09-15T20:31:00+08:00',raw,currency='CNY',adjustment='unadjusted_spot',calendar_gate={'allowed':True,'expected_date':'2026-09-15'})
    b['items'][0]['post_evidence']=[e];c=b['items'][0]['proposition_checks'][0];c.update(status='confirmed',evidence_ids=[e['evidence_id']])
    f=tmp_path/'batch';save(f,b)
    with pytest.raises(ValueError,match='price_cannot_prove_business'):w.apply(f,root)
