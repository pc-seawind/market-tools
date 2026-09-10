"""Reviewer reproductions; synthetic evidence never published as company research."""
from datetime import datetime,timezone,timedelta
import hashlib
import json
import pytest
from mt1.parallel import review_gate,timing
from mt1.parallel_bridge import bind_plan,assert_binding_current


def packet(tmp_path):
    p=tmp_path/'synthetic';p.write_text('SYNTHETIC')
    return dict(code='002396.SZ',channel='TREND',kind='risk',reviewer='test',reviewed_at='2026-09-11',valid_until='2026-09-12',conclusion='pass',reason='test',checks={k:'pass' for k in ('financial','liquidity','major_event','tradability')},sources=[dict(url='https://example.com',published_at='2026-09-09',path=str(p),sha256=hashlib.sha256(p.read_bytes()).hexdigest())])


@pytest.mark.parametrize('decision,price,reviewed,publication',[
 ('2026-09-11T08:00:00+08:00','20260910','2026-09-11','2026-09-09'),
 ('2026-09-11T08:00:00+08:00','20260910','2026-09-11T07:59:59+08:00','2026-09-11T02:00:00+08:00'),
 ('2026-09-11T21:00:00+08:00','20260911','2026-09-11T20:00:00+08:00','2026-09-11T19:00:00+08:00'),
 ('2026-09-12T10:00:00+08:00','20260911','2026-09-12T09:00:00+08:00','2026-09-11'),
 ('2026-09-13T10:00:00+08:00','20260911','2026-09-13T09:00:00+08:00','2026-09-12'),
 ('2026-09-11T00:00:00Z','20260910','2026-09-10T19:59:59-04:00','2026-09-10T23:00:00Z')])
def test_legitimate_decision_not_price_date(tmp_path,decision,price,reviewed,publication):
    p=packet(tmp_path);p.update(reviewed_at=reviewed,valid_until='2026-09-14');p['sources'][0]['published_at']=publication
    r=review_gate(p,'002396.SZ','TREND',price,'risk',decision_at=decision)
    assert r['status']=='pass' and r['times']['price_asof']!=r['times']['reviewed_at']


@pytest.mark.parametrize('field,value',[
 ('reviewed_at','2026-09-11T08:00:00.000001+08:00'),
 ('reviewed_at','2026-09-11T08:00:00'),
 ('reviewed_at','2026-09-11T08:00:00+15:00'),
 ('valid_until','2026-09-11T07:59:59+08:00'),
 ('published_at','2026-09-11T08:00:00.000001+08:00'),
 ('published_at','2026-09-11'),
 ('published_at','2026-09-11T08:00:00'),
 ('published_at','2026-09-11T01:00:00Z')])
def test_future_precision_timezone_and_expiry(tmp_path,field,value):
    p=packet(tmp_path);p['reviewed_at']='2026-09-11T08:00:00+08:00'
    if field=='published_at':p['sources'][0][field]=value
    else:p[field]=value
    assert review_gate(p,'002396.SZ','TREND','20260910','risk',decision_at='2026-09-11T08:00:00+08:00')['status']=='unknown'


def test_historical_replay_and_future_close(tmp_path):
    p=packet(tmp_path)
    assert review_gate(p,'002396.SZ','TREND','20260910','risk',decision_at='2026-09-10T21:00:00+08:00')['status']=='unknown'
    assert review_gate(p,'002396.SZ','TREND','20260911','risk',decision_at='2026-09-11T08:00:00+08:00')['status']=='unknown'


def test_near_ma20_does_not_claim_pullback():
    from test_mt11_parallel import tech
    t=tech();t.update(close=100,today_low=100,ma20=100,volume_ratio=1)
    assert timing(t,'20260910','20260910')['reasons']==['near_MA20_low_volume']


def test_held_deadline_and_exit_are_not_rewritten():
    p=dict(plan_id='x',version=1,code='x',state='HOLD',holding_status='confirmed',holding_evidence='user',original_deadline='2026-09-10',reference_price=100)
    b=bind_plan(p,'2026-09-11T08:00:00+08:00')
    assert b['deadline_status']=='due' and '不得自动延期' in b['held_action']
    assert p['original_deadline']=='2026-09-10' and b['bound_fields']['reference_price']==100
    p['state']='EXIT';assert '不自动重入' in bind_plan(p,'2026-09-11T08:00:00+08:00')['held_action']
    p['holding_evidence']=None;assert bind_plan(p,'2026-09-11T08:00:00+08:00')['holding_status']=='unknown'


def test_binding_stale_rejected(tmp_path):
    from mt1.store import Store
    path=tmp_path/'plans.db';s=Store(path)
    p=dict(plan_id='x',code='x',original_deadline='2026-10-10',state='WATCH')
    p=s.apply('plan:x',0,'a',p,'test',lambda old,v:v)
    obs={'binding':bind_plan(p,'2026-09-11T08:00:00+08:00')}
    assert assert_binding_current(obs,path)
    s.apply('plan:x',1,'b',p,'test',lambda old,v:v)
    with pytest.raises(ValueError):assert_binding_current(obs,path)
    s.close()


def test_partial_research_closes_to_bound_report_without_fake_final(tmp_path):
    import gzip
    from mt1.parallel_bridge import close_review
    from mt1.store import Store
    root=tmp_path/'out';root.mkdir();ledger=tmp_path/'ledger.db'
    s=Store(ledger)
    s.apply('plan:x',0,'x',dict(plan_id='x',code='002396.SZ',state='HOLD',holding_status='confirmed',holding_evidence='synthetic_user_confirmation',original_deadline='2026-09-10',reference_price=100),'test',lambda old,p:p);s.close()
    p=packet(tmp_path);p.update(kind='evidence',reviewed_at='2026-09-11T07:00:00+08:00',conclusion='partial',facts={'synthetic':1},remaining_checks=['valuation'])
    (root/'summary.json').write_text(json.dumps(dict(decision_at='2026-09-11T08:00:00+08:00',asof='20260910')))
    (root/'review-input.json').write_text(json.dumps([p]))
    with gzip.open(root/'funnel.json.gz','wt') as f:json.dump([{'code':'002396.SZ','channels':['TREND'],'plan':{'unheld':'研究未完成'}}],f)
    receipt=close_review(root,ledger)
    out=json.loads((root/'bound-review.json').read_text());o=out['observations'][0]
    assert o['review_input_results'][0]['gate']['status']=='partial'
    assert o['binding']['deadline_status']=='due'
    assert not o['final'] and not o['trade_intent']
    assert 'partial' in (root/'bound-review.md').read_text()
    assert receipt['confirmed_holdings']==1
    assert_binding_current(o,ledger)


def test_passed_research_still_no_final_shadow(tmp_path):
    p=packet(tmp_path)
    assert review_gate(p,'002396.SZ','TREND','20260910','risk',decision_at='2026-09-11T08:00:00+08:00')['status']=='pass'
    from test_mt1 import final_plan,method_lookup
    from mt1.plans import eligible
    plan=final_plan();plan.update(candidate_origin='mt11_parallel_shadow',method_status='shadow')
    assert not eligible(plan,datetime.now(timezone.utc).date(),method_lookup)
