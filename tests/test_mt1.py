import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import pytest
from mt1.calendar import gate
from mt1.store import Store
from mt1.plans import legacy_plan, reduce_plan, eligible, personal_return
from mt1.candidates import screen
from mt1.pipeline import run, migrate
from mt1.methods import reduce_method
from mt1.backtest import replay, REQUIREMENTS


def calendar(now, market='CN', holidays=()):
    tz,exchange,close={'CN':('Asia/Shanghai','SSE',15),'HK':('Asia/Hong_Kong','HKEX',16),'US':('America/New_York','NYSE',16)}[market]
    today=now.astimezone(ZoneInfo(tz)).date(); days=[]
    for i in range(45):
        d=today-timedelta(days=i)
        days.append({'date':str(d),'is_open':d.weekday()<5 and str(d) not in holidays,
                     'close_at':datetime(d.year,d.month,d.day,close,tzinfo=ZoneInfo(tz)).isoformat()})
    return {'exchange':exchange,'source':'synthetic exchange fixture','fetched_at':now.isoformat(),'days':days}


@pytest.mark.parametrize('phase,expected',[('morning','2026-09-09'),('evening','2026-09-10')])
def test_session_expectation(phase,expected):
    now=datetime.fromisoformat('2026-09-10T18:30:00+08:00')
    assert gate(calendar(now),'CN',phase,now)['expected_date']==expected


def test_holiday_weekend_failure_and_weekend_research():
    now=datetime.fromisoformat('2026-10-01T18:30:00+08:00')
    assert not gate(calendar(now,holidays=['2026-10-01']),'CN','evening',now)['allowed']
    assert not gate({},'CN','morning',now)['allowed']
    assert gate({},'CN','sunday',now)['allowed']
    now=datetime.fromisoformat('2026-09-12T18:30:00+08:00')
    assert gate(calendar(now),'CN','evening',now)['reason']=='market_closed'
    bad=calendar(now); bad['days'].pop(2)
    assert not gate(bad,'CN','morning',now)['allowed']


def test_foreign_completed_day_and_dst():
    now=datetime.fromisoformat('2026-09-10T18:30:00+08:00')
    assert gate(calendar(now,'US'),'US','evening',now)['expected_date']=='2026-09-09'
    assert gate(calendar(now,'HK'),'HK','evening',now)['expected_date']=='2026-09-10'
    bad=calendar(now); bad['fetched_at']='2020-01-01T00:00:00+00:00'
    assert not gate(bad,'CN','morning',now)['allowed']


def plan():
    p=legacy_plan({'code':'600000.SH','name':'示例'},'fixture','600000.SH')
    p['legacy_source']=None; p['episode']='episode-1'; p['plan_id']='p1'
    return p


def final_plan():
    p=plan(); today=date.today()
    p.update(state='BUY',qualification='final',channel='VALUE',unheld_direction='BUY',held_direction='HOLD',
        review_due=str(today+timedelta(days=30)),
        evidence=[{'url':'https://example.com/fixture','date':str(today),'claim':'synthetic only'}],
        milestones=[{'date':str(today+timedelta(days=30)),'condition':'synthetic verification'}],
        price_condition={'below':10,'basis':'synthetic','source_url':'https://example.com'},
        risk_boundary={'condition':'synthetic','source_url':'https://example.com'},invalidation='synthetic invalidation',
        checks={k:'pass' for k in ('financial','liquidity','major_event','technical','valuation')},
        reviewer='test',method_status='active')
    return p


def test_hold_preserves_original_baseline_and_cost():
    p=plan(); p.update(original_date='2026-07-01',original_deadline='2026-10-01',reference_price=100)
    new=reduce_plan(p,{'state':'HOLD','held_direction':'HOLD'})
    assert new['original_date']=='2026-07-01' and new['reference_price']==100
    assert personal_return(new,120) is None
    with pytest.raises(ValueError): reduce_plan(p,{'reference_price':110})
    with pytest.raises(ValueError): reduce_plan(p,{'actual_cost':100})
    with pytest.raises(ValueError): reduce_plan(p,{'review_due':'2026-11-01'})


def test_exit_requires_new_episode_and_substantive_evidence():
    p=plan()
    with pytest.raises(ValueError): reduce_plan(p,{'state':'EXIT','exit_basis':'sector_cold'})
    p=reduce_plan(p,{'state':'EXIT','exit_basis':'thesis_invalidated','evidence':[{'date':'2026-09-10','claim':'test'}]})
    with pytest.raises(ValueError): reduce_plan(p,{'state':'WATCH'})
    new=plan(); new.update(plan_id='p2',episode='episode-2')
    assert reduce_plan(None,new)['plan_id']=='p2'


def test_final_gate_future_data_shadow_and_hard_fail():
    p=final_plan(); assert eligible(p,date.today())
    assert reduce_plan(None,p)['state']=='BUY'
    for patch in ({'candidate_origin':'quality_value_shadow'}, {'checks':{'financial':'pass'}},
                  {'evidence':[{'url':'https://example.com','date':'2099-01-01','claim':'future'}]},
                  {'price_condition':'unknown'}):
        q={**p,**patch}
        assert not eligible(q,date.today())
        with pytest.raises(ValueError): reduce_plan(None,q)


def test_event_idempotence_conflict_append_only_and_concurrency(tmp_path):
    path=tmp_path/'plans.db'; store=Store(path); p=plan()
    first=store.apply('plan:p1',0,'r1',p,'initial',reduce_plan)
    assert store.apply('plan:p1',0,'r1',p,'initial',reduce_plan)==first
    with pytest.raises(ValueError): store.apply('plan:p1',0,'r1',p,'changed',reduce_plan)
    with pytest.raises(sqlite3.IntegrityError): store.db.execute('DELETE FROM events')
    store.close()
    def update(i):
        s=Store(path)
        try:
            s.apply('plan:p1',1,'u'+str(i),{'state':'HOLD'},'review',reduce_plan); return True
        except ValueError:return False
        finally:s.close()
    with ThreadPoolExecutor(max_workers=4) as pool: result=list(pool.map(update,range(4)))
    assert sum(result)==1


def universe():
    return {'asof':'2026-09-10','universe_size':100,'data_version':'fixture','observations':[
        {'stock':{'ts_code':'600000.SH','name':'fixture'},'daily':{'trade_date':'20260910','pe_ttm':'15','pb':'1','turnover_rate':'1'},
         'financials':[{'ann_date':'20260830','end_date':'20260630','roe':15,'eps':1,'ocfps':2,'debt_to_assets':30}]}]}


def test_quality_value_no_hot_coverage_missing_and_future_financials():
    u=universe(); r=screen(u)
    assert len(r['candidates'])==1 and r['coverage']==.01 and r['status']=='shadow'
    u['observations'][0]['financials'][0]['ann_date']='20260911'
    assert not screen(u)['candidates']
    u['observations'][0]['financials'][0]['ann_date']='20260910'
    assert not screen(u)['candidates']
    u['observations'][0]['financials'][0].pop('ann_date')
    assert not screen(u)['candidates']


def test_migration_keeps_raw_unknown_and_no_source_changes(tmp_path):
    rec=tmp_path/'rec.jsonl'; thesis=tmp_path/'thesis'; thesis.mkdir()
    text='{"id":"old","code":"600000.SH","action":"HOLD","price_at_rec":100}\n'
    rec.write_text(text); (thesis/'600000.SH.yaml').write_text('ticker: 600000.SH\nstatus: ACTIVE\n')
    s=Store(tmp_path/'s.db'); migrate(s,rec,thesis); migrate(s,rec,thesis)
    assert len(s.all())==1 and len(s.all('legacy:'))==2
    p=s.all()[0]; assert p['holding_status']=='unknown' and p['reference_price'] is None
    assert rec.read_text()==text
    s.close()


def test_pipeline_offline_four_modes_stale_and_resume(tmp_path):
    now=datetime.fromisoformat('2026-09-10T18:30:00+08:00')
    fx={'calendars':{'CN':calendar(now)},'recap':{'meta':{'trade_date':'2026-09-10','fresh':True,'errors':[]}},'universe':universe()}
    result=run('evening',tmp_path/'state',tmp_path/'investment',now,fx)
    assert result['quality_value']['coverage']==.01
    assert result['final_watchlist_candidates']==[]
    rerun=run('evening',tmp_path/'state',tmp_path/'investment',now,fx)
    assert result['report_path']!=rerun['report_path']
    morning=run('morning',tmp_path/'state',tmp_path/'investment',now,fx)
    assert any('stale' in e['error'] for e in morning['errors'])
    for phase in ('saturday','sunday'):
        r=run(phase,tmp_path/'state',tmp_path/'investment',now,{})
        assert r['gates']['CN']['allowed']
        if phase=='sunday': assert r['research_status']=='not_fetched' and r['previous_saturday']


def test_raw_candidate_watchlist_block_and_final_dry_run(tmp_path,monkeypatch):
    import watchlist_sync as w
    monkeypatch.setattr(w,'STATE_DIR',tmp_path)
    monkeypatch.setattr(w,'LEDGER_FILE',tmp_path/'ledger.jsonl')
    monkeypatch.setattr(w,'call_addwatchlist',lambda *a,**k:pytest.fail('no external write'))
    assert w.parse_recap_buys(tmp_path/'does-not-need-to-exist')==[]
    r=w.sync_buys([{'code':'600000.SH','name':'fixture'}],group='test',cooldown_days=30,dry_run=False,source='test')
    assert r['skipped'] and not r['added']
    p=final_plan()
    r=w.sync_buys([{'code':p['code'],'name':'fixture','_mt1_final_plan':p}],group='test',cooldown_days=30,dry_run=True,source='test')
    assert len(r['pending'])==1 and not (tmp_path/'ledger.jsonl').exists()


def test_methods_cannot_promote_without_fetch_or_validation():
    m=dict(method_id='m1',rule_version='1',sources=[dict(url='https://example.com',version='1',published_date='2026-09-01',
        fetched_at='2026-09-10',content_hash='test',fetch_status='ok')],rules={'entry':'test'},parameters={'x':1},
        horizon=[20,40,60],rollback='candidate',status='candidate',change_reason='test')
    m=reduce_method(None,m)
    with pytest.raises(ValueError): reduce_method(m,{'status':'active'})
    m=reduce_method(m,{'status':'shadow'})
    with pytest.raises(ValueError): reduce_method(m,{'status':'validated'})
    with pytest.raises(ValueError): reduce_method(m,{'rule_version':'2'})


def test_backtest_fails_closed_without_pit():
    r=replay({})
    assert r['metrics'] is None and r['status']=='unsupported' and 'pit_financials' in r['blockers']


def tape():
    dates=[str(date(2026,1,1)+timedelta(days=i)) for i in range(90)] # synthetic sessions, not real calendar
    return {'provenance':{k:{'verified':True,'artifact_hash':'synthetic-only'} for k in REQUIREMENTS},
        'sessions':dates,'fee_rate':.001,'slippage_rate':.001,
        'bars':[dict(code='test',date=d,open=100+i,close=100+i,buy_fillable=True,sell_fillable=True) for i,d in enumerate(dates)],
        'benchmark':{d:{'open':100,'close':100} for d in dates},
        'signals':[{'id':'1','code':'test','channel':'VALUE','date':dates[0],'exit_date':dates[10],
        'available_at':dates[0]+'T10:00:00+08:00','decision_at':dates[0]+'T16:00:00+08:00','exit_available_at':dates[10]+'T15:00:00+08:00','exit_decision_at':dates[10]+'T16:00:00+08:00','split':'oos'}]}


def test_paired_backtest_20_40_60_fees_next_fill_and_cash():
    data=tape(); data['bars'][1]['buy_fillable']=False
    r=replay(data)
    assert set(r['metrics'])=={'20','40','60'} and not r['oos_pass']
    assert r['samples'][0]['entry']==data['sessions'][2]
    assert r['metrics']['20']['paired_exit_minus_hold']<0
    assert r['metrics']['20']['exit']['capital_occupancy'] < r['metrics']['20']['hold']['capital_occupancy']
    data['bars']=data['bars'][1:]
    # Missing intermediate bars must not silently turn into tradable marks.
    data['bars'].pop(5)
    assert replay(data)['omitted']


def test_watchdog_cold_never_exit_or_position_instruction(monkeypatch,capsys):
    import rec_watchdog as w
    from types import SimpleNamespace
    monkeypatch.setattr(w,'score_sector',lambda s:SimpleNamespace(data_quality='ok',total_score=30,raw_signals={}))
    rec={'id':'r1','ts':date.today().isoformat(),'code':'600000.SH','action':'BUY','sector':'test','sector_tier1_score':70}
    alert=w.check_rec(rec)
    assert alert.suggestion=='REVIEW_THESIS'
    assert 'EXIT' not in w.print_summary([alert])
    assert len(w._active_latest_recs([{**rec,'action':'SELL'}]))==1


def test_watchlist_missing_ack_not_marked_success(tmp_path,monkeypatch):
    import watchlist_sync as w
    monkeypatch.setattr(w,'STATE_DIR',tmp_path)
    monkeypatch.setattr(w,'LEDGER_FILE',tmp_path/'ledger.jsonl')
    monkeypatch.setattr(w,'call_addwatchlist',lambda *a,**k:{'ok':True,'data':{}})
    p=final_plan()
    item={'code':p['code'],'name':'fixture','_mt1_final_plan':p}
    result=w.sync_buys([item,item],group='test',cooldown_days=30,dry_run=False,source='test')
    assert not result['added'] and not w.LEDGER_FILE.exists()


def test_calendar_foreign_failure_isolated(tmp_path,monkeypatch):
    import mt1.pipeline as p
    now=datetime.fromisoformat('2026-09-10T18:30:00+08:00')
    monkeypatch.setattr(p,'cn_calendar',lambda now:calendar(now))
    monkeypatch.setattr(p,'foreign_calendar',lambda *a:(_ for _ in ()).throw(ValueError('no permission')))
    r=p.run('morning',tmp_path/'s',tmp_path/'i',now)
    assert r['gates']['CN']['allowed'] and not r['gates']['HK']['allowed']
    assert any(e['stage']=='calendar_HK' for e in r['errors'])


def test_yaml_date_migration_and_finalize_empty_not_fake_review(tmp_path):
    from mt1.finalize import finalize
    t=tmp_path/'thesis';t.mkdir();(t/'600001.SH.yaml').write_text('ticker: 600001.SH\ndate: 2026-09-01\n')
    s=Store(tmp_path/'state/plans.db')
    r=migrate(s,tmp_path/'missing',t);assert r['imported']==1;s.close()
    out=finalize({'phase':'sunday','reviewer':'test','plan_events':[],
                  'research':{'fetch_status':'failed'}},tmp_path/'state',tmp_path/'investment')
    assert out['research_status']=='not_verified' and out['unreviewed_plan_ids']


def test_research_hash_mismatch_not_fetched(tmp_path):
    from mt1.finalize import finalize
    p=tmp_path/'source.txt'; p.write_text('test')
    b={'phase':'sunday','reviewer':'test','research':{'fetch_status':'ok','sources':[
        {'url':'https://example.com','fetched_at':'2026-09-10','content_path':str(p),'content_hash':'wrong'}]}}
    assert finalize(b,tmp_path/'state',tmp_path/'investment')['research_status']=='not_verified'


def test_stage_failure_retries_and_success_is_checkpointed(tmp_path,monkeypatch):
    import mt1.pipeline as p
    now=datetime.fromisoformat('2026-09-10T18:30:00+08:00')
    calls=[]
    def migrate_once(*a):
        calls.append(1)
        if len(calls)==1: raise ValueError('transient')
        return {'imported':0,'errors':[]}
    monkeypatch.setattr(p,'migrate',migrate_once)
    fx={'calendars':{'CN':calendar(now)},'recap':{'meta':{'trade_date':'20260910','fresh':True,'errors':[]}}}
    a=p.run('evening',tmp_path/'s',tmp_path/'i',now,fx)
    assert a['errors']
    b=p.run('evening',tmp_path/'s',tmp_path/'i',now,fx)
    c=p.run('evening',tmp_path/'s',tmp_path/'i',now,fx)
    assert not b['errors'] and not c['errors'] and len(calls)==2


def test_legacy_record_cannot_bypass_exit_or_direction_gate():
    p=legacy_plan({'code':'600000.SH','action':'BUY'},'fixture','600000.SH')
    with pytest.raises(ValueError):reduce_plan(p,{'state':'EXIT','held_direction':'EXIT'})
    with pytest.raises(ValueError):reduce_plan(p,{'unheld_direction':'BUY'})
    q=final_plan();q['review_due']=str(date.today()+timedelta(days=200))
    assert not eligible(q,date.today())
