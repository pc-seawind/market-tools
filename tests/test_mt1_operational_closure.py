from datetime import date,timedelta
import json
import importlib
from pathlib import Path
import pytest
from mt1.sweep import sweep,launch
from mt1.evidence import append,attach,inventory
from mt1.store import Store
from mt1.plans import reduce_plan
from test_mt1 import plan


def provider_fixture(name,**kw):
    if name=='stock_basic':return [{'ts_code':str(i),'name':'fixture'} for i in range(5)]
    if name=='daily_basic':return [{'ts_code':str(i),'trade_date':kw['trade_date'],'pe_ttm':10,'pb':1,'turnover_rate':2} for i in range(5)]
    if kw['ts_code']=='0':raise RuntimeError('simulated outage')
    return [{'ts_code':kw['ts_code'],'ann_date':str(date.today()-timedelta(days=1)),
        'end_date':str(date.today()-timedelta(days=2)),'roe':12,'ocfps':2,'eps':1,'debt_to_assets':30}]


def test_full_sweep_closes_all_and_retries_after_first_pass(tmp_path,monkeypatch):
    monkeypatch.setattr('mt1.sweep.time.sleep',lambda n:None)
    calls=[]
    def provider(n,**kw):
        if n=='fina_indicator':calls.append(kw['ts_code'])
        return provider_fixture(n,**kw)
    r=sweep(tmp_path,str(date.today()),batch_size=2,provider=provider)
    assert r['complete'] and r['attempted_unique']==5 and r['usable']==4 and r['unavailable']==1
    assert sorted(calls[:5])==['0','1','2','3','4'] and calls[5:]==['0']
    bad=json.loads((tmp_path/'sweeps'/str(date.today())/'symbols/0.json').read_text())
    assert len(bad['history'])==2 and bad['attempts']==2
    calls.clear()
    assert sweep(tmp_path,str(date.today()),provider=provider)['complete']
    assert calls==[]


def test_sweep_bounded_resume_and_date_isolation(tmp_path,monkeypatch):
    monkeypatch.setattr('mt1.sweep.time.sleep',lambda n:None)
    r=sweep(tmp_path,str(date.today()),batch_size=2,max_batches=1,provider=provider_fixture)
    assert r['attempted_unique']==2 and not r['complete']
    r=sweep(tmp_path,str(date.today()),batch_size=2,max_batches=1,provider=provider_fixture)
    assert r['attempted_unique']==4
    r=sweep(tmp_path,str(date.today()-timedelta(days=1)),batch_size=2,max_batches=1,provider=provider_fixture)
    assert r['attempted_unique']==2


def test_sweep_rejects_stale_daily(tmp_path):
    def stale(n,**kw):
        result=provider_fixture(n,**kw)
        if n=='daily_basic':result[0]['trade_date']='20000101'
        return result
    with pytest.raises(ValueError,match='date mismatch'):sweep(tmp_path,str(date.today()),provider=stale)


def test_launch_no_duplicate_if_process_lock_held(tmp_path):
    import fcntl
    root=tmp_path/'sweeps'/str(date.today());root.mkdir(parents=True)
    with (root/'lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        assert launch(tmp_path,str(date.today()))['status']=='already_running'


def test_delivery_requires_matching_run_hash_and_readback(tmp_path):
    rid='run-test';body=tmp_path/'provider.json';body.write_text('{}')
    readback=tmp_path/'readback';readback.write_text('wrong run')
    append(tmp_path,rid,'review',{'markdown':{'sha256':'abc'}})
    receipt=tmp_path/'receipt.json'
    def save():receipt.write_text(json.dumps({'run_id':rid,'document_url':'https://example.com',
        'report_sha256':'abc','provider_response_path':str(body),'readback_path':str(readback)}))
    save()
    with pytest.raises(ValueError,match='run_id'):attach(tmp_path,rid,'delivery',receipt)
    readback.write_text(rid)
    attach(tmp_path,rid,'delivery',receipt)
    assert inventory(tmp_path)['chains'][0]['missing']==['dispatch','run']


def test_coldstart_export_readonly_and_pending_report_no_fake_review(tmp_path):
    from mt1.coldstart import export
    from mt1.finalize import finalize
    s=Store(tmp_path/'plans.db');p=plan()
    s.apply('plan:p1',0,'p1',p,'real-shaped fixture',reduce_plan);s.close()
    out=export(tmp_path,tmp_path/'handoff',1)
    batch=json.loads((Path(out['out_dir'])/'batch-01.json').read_text())
    bundle=batch['response_template'];bundle.update(reviewer='engineering-gap-audit')
    r=finalize(bundle,tmp_path,tmp_path/'investment')
    assert r['pending_review_ids']==['p1'] and r['reviewed_plan_ids']==[] and r['changes']==[]
    assert 'WATCH' in Path(r['report_path']).read_text()
    s=Store(tmp_path/'plans.db');assert s.latest('plan:p1')['version']==1;s.close()


def test_stale_coldstart_fails_before_any_events(tmp_path):
    from mt1.finalize import finalize
    p=plan()
    with pytest.raises(ValueError,match='stale'):
        finalize({'phase':'morning','reviewer':'test',
            'review_items':[{'plan_id':'missing','expected_version':1,'status':'pending','note':'test'}],
            'plan_events':[{'id':'p1','expected_version':0,'request_id':'p1','reason':'test','payload':p}]},
            tmp_path,tmp_path/'investment')
    s=Store(tmp_path/'plans.db');assert not s.all();s.close()


def test_audit_module_importable_and_does_not_claim_metrics():
    from mt1.data_readiness import PROBES
    assert {'income','suspend_d','fina_indicator_vip','stock_basic'} <= {n for n,p in PROBES}


def test_backfill_checkpoint_and_no_returns(tmp_path):
    from mt1.data_readiness import backfill
    manifest={'tasks':[{'id':str(i),'api':'daily','params':{'ts_code':str(i)}} for i in range(3)]}
    calls=[]
    def fetch(name,**params):calls.append(params['ts_code']);return [{'ts_code':params['ts_code']}]
    a=backfill(tmp_path,manifest,1,fetch)
    assert a['pending']==2 and a['metrics'] is None
    b=backfill(tmp_path,manifest,100,fetch)
    assert b['pending']==0 and calls==['0','1','2']
    assert backfill(tmp_path,manifest,100,fetch)['requests_this_run']==0


def test_evidence_inventory_detects_changed_artifact(tmp_path):
    from mt1.evidence import file_ref
    p=tmp_path/'report';p.write_text('original')
    append(tmp_path,'run-test','run',{'report':file_ref(p)})
    p.write_text('changed')
    r=inventory(tmp_path)
    assert r['chains'][0]['integrity_errors'][0]['error']=='hash_changed'
    assert all(x['status']=='awaiting_natural_chain' for x in r['phases'].values())


def test_migration_preserves_metadata_raw_without_new_fake_plan(tmp_path):
    from mt1.pipeline import migrate
    t=tmp_path/'thesis';t.mkdir();(t/'_bootstrap_state.yaml').write_text('last_run: 2026-09-11\n')
    s=Store(tmp_path/'plans.db')
    try:
        r=migrate(s,tmp_path/'absent.jsonl',t)
        assert r['imported']==0 and len(r['ignored_metadata'])==1 and not r['errors']
        assert s.all()==[] and len(s.all('legacy:thesis:'))==1
    finally:s.close()


def test_handoff_export_does_not_overwrite_investment_edits(tmp_path):
    from mt1.coldstart import export
    s=Store(tmp_path/'plans.db');s.apply('plan:p1',0,'p1',plan(),'test',reduce_plan);s.close()
    r=export(tmp_path,tmp_path/'out')
    path=Path(r['out_dir'])/'batch-01.json';path.write_text('{"user_edit":true}')
    with pytest.raises(ValueError,match='immutable'):export(tmp_path,tmp_path/'out')
    assert json.loads(path.read_text())=={'user_edit':True}
