"""Regression of independent investment review; only temporary synthetic state."""
import copy
from datetime import timedelta
import pytest
import test_research_pool_lifecycle as base
from mt1.research_pool import apply, snapshot, research_queues, review_queue, report
from mt1.scope import load, filter_plans
from mt1.daily_tracking import track


@pytest.fixture
def case():
    c=base.ResearchPool(); c.setUp()
    try: yield c
    finally: c.doCleanups()


def pending(c, **kw):
    # Deliberately minimal patch: no copied next_review / original material.
    e={k: c.e[k] for k in ('scope_epoch','scope_sha256','domain','reviewer','code','reviewed_at')}
    e.update(request_id='pending-review',expected_version=1,state='pending',reason='source unavailable',
             gap_or_exit_basis='source unavailable',research_outcome='data_blocked')
    e.update(kw)
    return apply(c.scope,e,c.now)


def test_pending_monitors_without_formal_admission(case):
    before=case.put(); after=pending(case)
    snap=snapshot(case.scope,'synthetic-reset')
    assert len(snap['monitor_rows'])==1
    assert snap['monitor_rows'][0]['state']=='pending'
    assert after['next_review']==before['next_review']
    scope=load(case.scope)
    assert not scope['candidates'] and not scope['recommendations']
    assert filter_plans([{'code':case.e['code'],'scope_epoch':case.e['scope_epoch'],'research_event_id':'pending-review'}],scope)==[]
    r=track(str(case.scope),fetch=lambda c:[{'date':'20261009','close':10}],gates={'CN':{'expected_date':'2026-10-09'}})
    row=next(r for r in r['rows'] if r['code']==case.e['code'])
    assert row['research_pool_state']=='pending' and row['research_monitor_episodes'][0]['episode_id']=='test1'


def test_exit_then_reentry_keeps_both_episodes_in_daily(case):
    case.put()
    case.put(request_id='exit',expected_version=1,state='exited',gap_or_exit_basis='verified failure')
    later=case.now+timedelta(seconds=1)
    apply(case.scope,{**case.e,'reviewed_at':later.isoformat(),'request_id':'reentry','expected_version':2},later)
    snap=snapshot(case.scope,'synthetic-reset')
    assert len(snap['episodes'])==2
    assert {(r['episode_id'],r['state']) for r in snap['monitor_rows']}=={('test1','exited'),('reentry','watch')}
    r=track(str(case.scope),fetch=lambda c:[{'date':'20261009','close':10}],gates={'CN':{'expected_date':'2026-10-09'}})
    row=next(r for r in r['rows'] if r['code']==case.e['code'])
    assert row['research_pool_state']=='watch' and len(row['research_monitor_episodes'])==2
    text=report(load(case.scope))
    assert 'test1' in text and '退出后反事实' in text and 'unknown' in text


def test_thousands_of_new_discoveries_do_not_starve_due_review(case):
    case.put(); pending(case)
    rows=[{'code':str(n)+'.SZ','channel_risk':{'TREND':{'status':'unknown'}}} for n in range(4375)]
    scope=load(case.scope)
    assert len(research_queues(rows,rows,scope)[1])==10
    # Due/blocked company need not still pass the discovery screen to be reviewed.
    q=review_queue(scope,rows)
    assert len(q)==1 and q[0]['code']==case.e['code'] and q[0]['research_gap']=='outside_panel'
    assert q[0]['original_due']==case.e['next_review']
    assert review_queue(scope,rows)==q


def test_recorded_attempt_rotates_not_due_date(case):
    case.put(); pending(case)
    scope=load(case.scope)
    r=scope['research_pool']['rows'][0]
    scope['research_pool']['rows'] += [{**r,'code':'other.SZ','reviewed_at':(case.now-timedelta(days=1)).isoformat()}]
    assert review_queue(scope,limit=1)[0]['code']=='other.SZ'


def test_near_catalyst_and_separate_horizon(case):
    near=(case.now+timedelta(days=3)).isoformat()
    end=(case.now+timedelta(days=60)).isoformat()
    r=case.put(milestones=[{'description':'synthetic upcoming filing','at':near}],research_horizon_end=end)
    assert r['milestones'][0]['at']==near and r['research_horizon_end']==end
    assert r['original']['research_horizon_end']==end


@pytest.mark.parametrize('days,horizon',[(0,60),(-1,60),(61,60),(5,5),(5,94)])
def test_invalid_catalyst_or_horizon(case,days,horizon):
    with pytest.raises(ValueError):
        case.put(milestones=[{'description':'test','at':(case.now+timedelta(days=days)).isoformat()}],
                 research_horizon_end=(case.now+timedelta(days=horizon)).isoformat())


def test_exit_pending_investigation_does_not_reopen_closed_episode(case):
    case.put();case.put(request_id='exit',expected_version=1,state='exited',gap_or_exit_basis='failure')
    pending(case,request_id='new-investigation',expected_version=2)
    assert len(snapshot(case.scope,'synthetic-reset')['monitor_rows'])==1
    later=case.now+timedelta(seconds=2)
    apply(case.scope,{**case.e,'request_id':'new','expected_version':3,'reviewed_at':later.isoformat()},later)
    assert len(snapshot(case.scope,'synthetic-reset')['episodes'])==2


def test_review_does_not_extend_original_horizon(case):
    first=case.put()
    later=case.now+timedelta(days=1)
    event={**case.e,'request_id':'maintain','expected_version':1,'reviewed_at':later.isoformat(),
           'milestones':[{'description':'near node','at':(later+timedelta(days=1)).isoformat()}]}
    after=apply(case.scope,event,later)
    assert after['research_horizon_end']==first['research_horizon_end']
    with pytest.raises(ValueError,match='silently changed'):
        apply(case.scope,{**event,'expected_version':2,'request_id':'extend','research_horizon_end':(later+timedelta(days=93)).isoformat()},later)


def test_real_cli_event_to_readonly_performance_with_isolated_synthetic_sources(case):
    import hashlib,json,subprocess,sys
    from pathlib import Path
    earlier=case.now-timedelta(days=4)
    start=earlier.date().isoformat();end=(case.now-timedelta(days=1)).date().isoformat()
    contract={'anchor_date':start,'target_dates':[end],'currency':'CNY','benchmark_code':'TESTINDEX',
              'basis':'unadjusted_close','anchor_policy':'close_observation_not_execution',
              'fee_scenario':'synthetic 10bps','fees':{'entry_bps':10,'exit_bps':10}}
    e=copy.deepcopy(case.e)
    e.update(state='recommended',reviewed_at=earlier.isoformat(),valuation='synthetic scenario',
             risk_signoff={'reviewer':'test','status':'pass','at':earlier.isoformat()},
             valuation_signoff={'reviewer':'test','status':'pass','at':earlier.isoformat()},performance_contract=contract)
    e['sources'][0]['published_at']=(earlier-timedelta(days=1)).isoformat();e['sources'][0]['fetched_at']=earlier.isoformat()
    apply(case.scope,e,earlier)
    def source(name,data):
        p=case.root/name;p.write_text(json.dumps(data));return {'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),
             'url':'https://example.invalid/SYNTHETIC/'+name,'fetched_at':case.now.isoformat()}
    stock={'code':e['code'],'currency':'CNY','basis':'unadjusted_close','kind':'stock',
           'rows':[{'date':start,'close':100,'status':'traded'},{'date':end,'close':110,'status':'traded'}]}
    index={**stock,'code':'TESTINDEX','kind':'index','index_type':'price_index'}
    bundle={'scope_epoch':e['scope_epoch'],'asof':end,'series':[{'code':e['code'],'source':source('quotes',stock)},
             {'code':'TESTINDEX','source':source('index',index)}],
             'corporate_actions':[{'code':e['code'],'source':source('actions',{'code':e['code'],'status':'verified','from':start,'through':end,'events':[]})}]}
    inp=case.root/'observations.json';inp.write_text(json.dumps(bundle))
    before=case.scope.read_bytes();out=case.root/'audit'
    cp=subprocess.run([sys.executable,'mt1.py','--scope',str(case.scope),'research-performance','--observations',str(inp),'--out',str(out)],text=True,capture_output=True,timeout=15)
    assert cp.returncode==0,cp.stderr
    result=json.loads((out/'performance.json').read_text());row=result['results'][0]
    assert row['status']=='verified_price_scenario'
    assert row['net_return']==pytest.approx(110*.999/(100*1.001)-1)
    assert len(list((out/'sources').iterdir()))==3
    assert '10.00%' in (out/'report.md').read_text()
    assert case.scope.read_bytes()==before and len(snapshot(case.scope,e['scope_epoch'])['events'])==1
    receipt=json.loads((out/'receipt.json').read_text())
    assert all(hashlib.sha256((out/name).read_bytes()).hexdigest()==h for name,h in receipt['files'].items())
