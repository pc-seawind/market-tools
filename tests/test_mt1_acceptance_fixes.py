from datetime import date, timedelta
import pytest
from test_mt1 import plan, final_plan, method_lookup
from mt1.plans import reduce_plan, eligible, deadline_errors

@pytest.mark.parametrize('action', ['SELL','EXIT'])
@pytest.mark.parametrize('qualification', ['pending_review','final'])
def test_exit_does_not_require_buy_gates(action, qualification):
    p=final_plan();p['checks']={'financial':'fail','valuation':'fail'}
    p.update(state=action,held_direction=action,unheld_direction='WATCH',
             qualification=qualification,exit_basis='thesis_invalidated',original_deadline='2020-01-01')
    assert reduce_plan(None,p)['state']==action

@pytest.mark.parametrize('evidence', [['unverified'], [], [{'url':'bad','date':'2020-01-01','claim':'x'}],
    [{'url':'https://example.com','date':'2099-01-01','claim':'x'}]])
def test_exit_pending_rejects_bad_evidence(evidence):
    p=plan();p.update(state='EXIT',held_direction='EXIT',exit_basis='thesis_invalidated',evidence=evidence,reviewer='test')
    with pytest.raises(ValueError):reduce_plan(None,p)


def test_legacy_exemption_is_only_explicit_migration():
    from mt1.plans import legacy_plan
    p=legacy_plan({'code':'a','action':'EXIT'},'fixture','a');p.update(state='EXIT',held_direction='EXIT')
    with pytest.raises(ValueError):reduce_plan(None,p)
    assert reduce_plan(None,p,legacy_import=True)['state']=='EXIT'


def test_method_binding_and_withdrawal():
    p=final_plan()
    assert not eligible(p,date.today())
    assert eligible(p,date.today(),method_lookup)
    for patch in ({'method_id':None},{'method_version':'2'}):
        assert not eligible({**p,**patch},date.today(),method_lookup)
    assert not eligible(p,date.today(),lambda key:{'status':'shadow','rule_version':'1'})


def test_deadline_extension_is_evidence_versioned_and_original_immutable():
    p=final_plan();p.update(original_date=str(date.today()-timedelta(days=91)),
                           original_deadline=str(date.today()-timedelta(days=1)),version=4)
    assert not eligible(p,date.today(),method_lookup)
    ext=dict(deadline=str(date.today()+timedelta(days=60)),reviewed_at=str(date.today()),
             reviewer='test',reason='new thesis evidence',plan_version=4,evidence=p['evidence'])
    q=reduce_plan(p,{'deadline_extension':ext},method_lookup)
    assert eligible(q,date.today(),method_lookup) and q['original_deadline']==p['original_deadline']
    with pytest.raises(ValueError):reduce_plan(p,{'deadline_extension':{**ext,'plan_version':3}},method_lookup)
    with pytest.raises(ValueError):reduce_plan(p,{'deadline_extension':{**ext,'evidence':['unverified']}},method_lookup)
    with pytest.raises(ValueError):reduce_plan(p,{'original_deadline':ext['deadline']},method_lookup)
    assert deadline_errors(plan(),date.today())


def test_hold_has_own_thesis_not_buy_valuation():
    p=final_plan();p.update(state='HOLD',unheld_direction='WATCH',holding_thesis='orders intact',holding_thesis_status='valid')
    p['checks']['valuation']='fail'
    assert reduce_plan(None,p)['state']=='HOLD'
    p['holding_thesis_status']='unknown'
    with pytest.raises(ValueError):reduce_plan(None,p)


def test_universe_batches_advance_across_dates_and_retry_failures(tmp_path,monkeypatch):
    from mt1 import data
    def api(name,**kw):
        if name=='stock_basic':return [{'ts_code':str(i),'name':str(i)} for i in range(5)]
        if name=='daily_basic':return [{'ts_code':str(i)} for i in range(5)]
        if kw['ts_code']=='0':raise ValueError('no financials')
        return [{'ann_date':'20260101'}]
    monkeypatch.setattr(data,'api',api)
    a=data.collect_universe('2026-09-10',tmp_path,2)
    b=data.collect_universe('2026-09-10',tmp_path,2)
    c=data.collect_universe('2026-09-11',tmp_path,2)
    assert a['batch_codes']==['0','1'] and b['batch_codes']==['2','3'] and c['batch_codes']==['4','0']
    assert b['coverage_progress']['current_date_observed']==3
    assert c['coverage_progress']['attempted_unique']==5
    assert c['coverage_progress']['current_date_observed']==1


def test_baseline_registration_is_explicit_and_retry_cannot_reactivate(tmp_path):
    from mt1.methods import register_existing, reduce_method
    from mt1.store import Store
    for name in ('sector_picks.py','sector_score.py','grading.py'):(tmp_path/name).write_text('# fixture')
    s=Store(tmp_path/'s.db')
    try:
        m=register_existing(s,tmp_path,'migration-test')
        assert m['status']=='active' and m['validation_status']=='not_revalidated'
        s.apply('method:'+m['method_id'],1,'withdraw',{'status':'shadow','change_reason':'withdraw'},'withdraw',reduce_method)
        assert register_existing(s,tmp_path,'migration-test')['status']=='shadow'
    finally:s.close()


def test_new_ledger_verify_survives_exit_and_is_idempotent(tmp_path):
    from mt1.verify import verify
    from mt1.store import Store
    p=final_plan();p.update(original_date=str(date.today()-timedelta(days=5)),reference_price=10)
    s=Store(tmp_path/'plans.db')
    s.apply('plan:p1',0,'buy',p,'fixture',lambda old,p:reduce_plan(old,p,method_lookup))
    s.apply('plan:p1',1,'exit',{'state':'EXIT','held_direction':'EXIT','unheld_direction':'WATCH',
                             'exit_basis':'thesis_invalidated'},'fixture',reduce_plan)
    s.close()
    def fetch(p,asof):return [{'ts_code':p['code'],'trade_date':str(asof),'close':'12'}]
    a=verify(tmp_path,fetch=fetch)
    b=verify(tmp_path,fetch=lambda *a:pytest.fail('idempotent no refetch'))
    assert a['marks']==b['marks'] and a['verified']==1
    assert a['marks'][0]['quoted_return']==pytest.approx(.2)
    assert a['marks'][0]['entry_plan_version']==1
    assert a['marks'][0]['horizons']['20'] is None


@pytest.mark.parametrize('phase',['morning','evening','saturday','sunday'])
def test_four_phase_finalize_reads_real_registry_and_withdrawal(tmp_path,monkeypatch,phase):
    from mt1.finalize import finalize
    import importlib
    module=importlib.import_module('mt1.finalize')
    from mt1.methods import register_existing
    from mt1.store import Store
    from test_mt1 import calendar
    for name in ('sector_picks.py','sector_score.py','grading.py'):(tmp_path/name).write_text('# fixture')
    monkeypatch.setattr(module,'cn_calendar',lambda now:calendar(now))
    monkeypatch.setattr(module,'gate',lambda *args:{'allowed':True})
    state=tmp_path/'state';s=Store(state/'plans.db');m=register_existing(s,tmp_path,'test');s.close()
    p=final_plan();p.update(method_id=m['method_id'],method_version=m['rule_version'])
    bundle={'phase':phase,'reviewer':'test','reviewed_plan_ids':['p1'],
            'plan_events':[{'id':'p1','expected_version':0,'request_id':'new','reason':'fixture','payload':p}]}
    result=finalize(bundle,state,tmp_path/'investment')
    assert not result['errors'] and len(result['watchlist']['pending'])==1
    assert 'BUY' in __import__('pathlib').Path(result['report_path']).read_text()
    withdrawal={'phase':phase,'reviewer':'test','method_events':[{'id':m['method_id'],'expected_version':1,
                'request_id':'withdraw','reason':'fixture','payload':{'status':'shadow','change_reason':'withdraw'}}]}
    assert finalize(withdrawal,state,tmp_path/'investment')['watchlist']['pending']==[]


def test_existing_daily_verify_entrypoint_calls_both_ledgers(monkeypatch,capsys):
    import rec_log
    import mt1.verify
    from types import SimpleNamespace
    calls=[]
    monkeypatch.setattr(mt1.verify,'verify',lambda **kw:calls.append('mt1') or {'verified':0})
    monkeypatch.setattr(rec_log,'verify_all',lambda **kw:calls.append('legacy') or dict(total=0,verified=0,expired=0,fetch_failed=0))
    rec_log._cmd_verify(SimpleNamespace(rec_id=None,date=None))
    assert calls==['mt1','legacy'] and 'MT1_VERIFY_JSON=' in capsys.readouterr().out


def test_exit_updates_cannot_replace_evidence_with_unverified():
    p=final_plan();p.update(state='EXIT',held_direction='EXIT',unheld_direction='WATCH',exit_basis='risk_boundary',qualification='pending_review')
    p=reduce_plan(None,p)
    with pytest.raises(ValueError):reduce_plan(p,{'evidence':['unverified']})
