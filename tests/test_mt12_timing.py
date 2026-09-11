"""Synthetic boundary tests only; never efficacy or real holding evidence."""
import copy
import json
from datetime import date, timedelta
from pathlib import Path
import pytest
from mt1.timing import contract, digest, evaluate, normalize, indicators, step, relative_strength
from mt1.timing_experiment import next_fill, compare, trade
from mt1.timing_cli import observe

ASOF='2026-09-11T13:00:00+00:00'


@pytest.fixture(autouse=True)
def fixed_observation_clock(monkeypatch):
    # Synthetic calendars cover ASOF, not the machine's changing wall date.
    # Keep production calendar-refresh checks intact and test them explicitly.
    import mt1.timing_cli as cli
    from datetime import datetime
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromisoformat(ASOF).astimezone(tz)
    monkeypatch.setattr(cli, 'datetime', Clock)


def fixture(n=120, market='CN'):
    rows=[];d=date(2024,1,2)
    for i in range(n):
        while d.weekday()>4: d+=timedelta(days=1)
        c=100+i*.05; width=.3 if i>=115 else 1
        rows.append({'date':str(d),'open':c-.1,'high':c+width,'low':c-width,'close':c,'vol':100,
                     'factor':1, 'close_at':str(d)+'T17:00:00+08:00', 'open_at':str(d)+'T09:30:00+08:00',
                     'execution':{'buy':True,'sell':True,'settlement_ok':True,'known_at':str(d)+'T09:00:00+08:00','source':'SYNTHETIC_ONLY'}})
        d+=timedelta(days=1)
    return {'code':'TEST','market':market,'bars':rows,'sessions':[b['date'] for b in rows],'expected_date':rows[-1]['date'],
            'adjustment':'vendor_factor_verified','basis_id':'SYNTHETIC_CONSTANT_UNIT','calendar_verified':True,'calendar_coverage_through':'2026-09-11',
            'fetched_at':ASOF,'benchmarks':{'broad':{'id':contract()['benchmarks'][market],'market':market,'fetched_at':ASOF,'price_basis':'compatible_price_return',
                                                   'bars':[{'date':b['date'],'close':100} for b in rows]}}}


def append(p, close=None, vol=100, factor=1, width=.3):
    p=copy.deepcopy(p); b=copy.deepcopy(p['bars'][-1]);d=date.fromisoformat(b['date'])+timedelta(days=1)
    while d.weekday()>4: d+=timedelta(days=1)
    c=close or b['close']+.05
    b.update(date=str(d),open=c-.1,high=c+width,low=c-width,close=c,vol=vol,factor=factor,
             close_at=str(d)+'T17:00:00+08:00',open_at=str(d)+'T09:30:00+08:00')
    b['execution']['known_at']=str(d)+'T09:00:00+08:00'
    p['bars'].append(b);p['sessions'].append(str(d));p['expected_date']=str(d)
    p['benchmarks']['broad']['bars'].append({'date':str(d),'close':100})
    return p


def test_first_observation_not_cost_or_backfill():
    p=fixture();r=evaluate(p,asof=ASOF)
    assert r['status']=='ok'
    assert r['actual_cost'] is None and r['actual_entry_date'] is None and r['personal_pnl'] is None
    assert r['state']['monitor']['highest']==p['bars'][-1]['close']
    assert r['state']['monitor']['structure_known_date']==p['bars'][-2]['date']
    assert r['state']['observed_sessions']==1
    assert not r['final_buy'] and not r['formal_sell']
    assert all(x['status']=='not_matured' for x in r['horizons'])


def test_setup_then_later_breakout_not_same_day():
    p=fixture();r=evaluate(p,asof=ASOF)
    assert r['entry']['breakout']['status']=='setup'
    assert r['entry']['pullback']['status']=='setup'
    q=append(p,108,200);r2=evaluate(q,asof=ASOF,previous=r['state'])
    assert r2['entry']['breakout']['status']=='trigger'
    assert r2['state']['breakout_episode']['level']==r['entry']['breakout']['level']
    assert r2['entry']['breakout']['formed_date']<r2['entry']['breakout']['trigger_date']


def test_pullback_confirmation_and_value_reversal_no_ma60_veto():
    p=fixture();p['bars'][-1]['vol']=80
    for channel in ('VALUE','REVERSAL'):
        r=evaluate(p,asof=ASOF,channel=channel)
        q=append(p,107,90)
        r2=evaluate(q,asof=ASOF,previous=r['state'],channel=channel)
        assert r2['entry']['pullback']['status']=='trigger'


def test_failure_requires_registered_episode_and_two_closes():
    p=fixture();r=evaluate(p,asof=ASOF)
    p=append(p,108,200);r=evaluate(p,asof=ASOF,previous=r['state'])
    p=append(p,105);r1=evaluate(p,asof=ASOF,previous=r['state'])
    assert 'registered_breakout_failed' not in r1['risk']['reasons']
    p=append(p,105);r2=evaluate(p,asof=ASOF,previous=r1['state'])
    assert 'registered_breakout_failed' in r2['risk']['reasons']
    fresh=evaluate(p,asof=ASOF)
    assert 'registered_breakout_failed' not in fresh['risk']['reasons']


def test_expiry_counts_sessions_not_weeks():
    p=fixture();r=evaluate(p,asof=ASOF)
    for i in range(11):
        p=append(p,105.95,90)
        r=evaluate(p,asof=ASOF,previous=r['state'])
    assert r['entry']['breakout']['status']=='cancel'
    assert r['entry']['breakout']['reason']=='setup_expired'
    assert r['state']['observed_sessions']==12
    assert len(r['state']['condition_history'])==12


def test_risk_independent_of_unheld_qualification_and_latched_reason():
    p=fixture();r=evaluate(p,asof=ASOF)
    p=append(p,80);r=evaluate(p,asof=ASOF,previous=r['state'])
    assert r['risk']['status']=='risk_exit_trigger'
    assert r['entry']['breakout']['status']=='cancel'
    original=copy.deepcopy(r['risk']['original_trigger'])
    p=append(p,115);r=evaluate(p,asof=ASOF,previous=r['state'])
    assert r['risk']['original_trigger']==original


def test_atr_simple_mean_no_loosening_and_same_bar_unknown():
    p=fixture();r=evaluate(p,asof=ASOF); stop=r['atr_stop']
    p=append(p,106,width=20);r2=evaluate(p,asof=ASOF,previous=r['state'])
    assert r2['atr_stop']>=stop
    assert r2['risk']['intrabar_order']=='unknown_no_intrabar_fill'
    tr=[max(b['high']-b['low'],abs(b['high']-p['bars'][i-1]['close']),abs(b['low']-p['bars'][i-1]['close'])) for i,b in enumerate(p['bars']) if i]
    assert r2['atr20']==pytest.approx(sum(tr[-20:])/20)


def test_split_constant_unit_not_max_raw_stop():
    p=fixture();r=evaluate(p,asof=ASOF)
    q=append(p,53,factor=2,width=.15);r2=evaluate(q,asof=ASOF,previous=r['state'])
    assert r2['status']=='ok'
    assert r2['atr_stop']<r['atr_stop']*.6  # raw displayed scale changed, internal did not loosen.
    assert r2['state']['monitor']['atr_stop']>=r['state']['monitor']['atr_stop']
    assert 'initial_structure_invalidated' not in r2['risk']['reasons']
    q['bars'][0]['factor']=2
    assert evaluate(q,asof=ASOF,previous=r['state'])['status']=='blocked'


@pytest.mark.parametrize('mutation', ['future','missing','duplicate','factor','calendar','basis','ohlc','foreign','warmup'])
def test_invalid_inputs_fail_closed(mutation):
    p=fixture()
    if mutation=='future': p['bars'][-1]['close_at']='2027-01-01T00:00:00+00:00'
    elif mutation=='missing': p['bars'].pop(40)
    elif mutation=='duplicate': p['bars'].append(p['bars'][-1])
    elif mutation=='factor': p['bars'][-1]['factor']=None
    elif mutation=='calendar': p['calendar_verified']=False
    elif mutation=='basis': p['adjustment']='qfq_unverified'
    elif mutation=='ohlc': p['bars'][-1]['high']=2
    elif mutation=='foreign': p['bars'][-1]['code']='BAD'
    elif mutation=='warmup': p=fixture(80)
    r=evaluate(p,asof=ASOF)
    assert r['status']=='blocked' and r['atr_stop'] is None


@pytest.mark.parametrize('market',['CN','HK','US'])
def test_market_benchmark_unknown_not_zero_or_csi300(market):
    p=fixture(market=market);p['benchmarks']={}
    r=evaluate(p,asof=ASOF)
    assert r['rs']['broad']['value'] is None and r['rs']['industry']['value'] is None
    assert r['old_entry']['status']=='unknown'
    if market!='CN':
        p=fixture(market=market);p['benchmarks']['broad']['id']='000300.SH'
        assert evaluate(p,asof=ASOF)['old_entry']['status']=='unknown'


def test_industry_requires_membership_provenance():
    p=fixture();p['benchmarks']['industry']=copy.deepcopy(p['benchmarks']['broad'])
    assert evaluate(p,asof=ASOF)['rs']['industry']['status']=='unknown'
    p['benchmarks']['industry'].update(id='TEST_INDUSTRY',membership_basis='SYNTHETIC_PIT',membership_known_at=ASOF)
    assert evaluate(p,asof=ASOF)['rs']['industry']['status']=='ok'


def test_repeat_and_future_source_and_changed_channel():
    p=fixture();r=evaluate(p,asof=ASOF)
    r2=evaluate(p,asof=ASOF,previous=r['state'])
    assert r2['idempotent'] and r2['state']==r['state']
    assert evaluate(p,asof=ASOF,previous=r['state'],channel='VALUE')['status']=='blocked'
    assert evaluate(p,asof='2026-09-10T00:00:00+00:00')['status']=='blocked'


def test_next_open_gap_suspension_limits_settlement_and_unknown():
    p=fixture(125);rows=normalize(p,ASOF);cfg=contract()
    rows[120]['open']=80
    assert next_fill(rows,119,'sell','CN',cfg)['price']==80
    rows[120]['execution']['sell']=False  # verified suspended/limit-locked
    rows[121]['execution']['settlement_ok']=False
    assert next_fill(rows,119,'sell','CN',cfg)['index']==122
    rows[120]['execution']={}
    assert next_fill(rows,119,'sell','CN',cfg)['status']=='unknown'
    assert next_fill(rows,124,'sell','CN',cfg)['status']=='pending'
    rows[120]['execution']={'buy':True,'sell':True,'settlement_ok':True,'known_at':'2027-01-01T00:00:00Z','source':'late'}
    assert next_fill(rows,119,'buy','HK',cfg)['reason']=='execution_evidence_after_open'


def test_horizon_cross_week_matures_without_personal_pnl():
    p=fixture();r=evaluate(p,asof=ASOF)
    for i in range(20):
        p=append(p);r=evaluate(p,asof=ASOF,previous=r['state'])
    assert r['horizons'][0]['status']=='observed_price_only'
    assert r['horizons'][1]['status']=='not_matured' and r['personal_pnl'] is None


def experiment_fixture():
    p=fixture(420)
    # deterministic boom/reversal episodes, known synthetic execution flags
    for i,b in enumerate(p['bars']):
        c=100+(i%80)*.15 if i%80<50 else 110-(i%80-50)*1.2
        w=.3 if i%20>=15 else 1
        b.update(open=c-.1,high=c+w,low=c-w,close=c,vol=180 if i%20==0 else 90)
    # Dedicated branch-coverage scenario, not selected for investment returns.
    p['channel']='VALUE'
    b=p['bars'][180];c=max(r['high'] for r in p['bars'][160:180])+2
    b.update(open=c-.1,high=c+.3,low=c-.3,close=c,vol=250)
    return {'source_kind':'SYNTHETIC_CORRECTNESS_ONLY','scope_epoch':'test','scope_codes':['TEST'],'panels':[p],'asof':ASOF,
            'entry_episodes':[{'id':'synthetic-entry-119','code':'TEST','signal_date':p['bars'][119]['date'],'source_hash':digest(p['bars'][:120])}]}


def test_ab_fixed_entries_exits_purge_metrics_and_unknown():
    b=experiment_fixture();r=compare(b)
    a=[t for t in r['trades'] if t['group']=='A']
    assert len(a)==3 and len({t['entry_fill']['date'] for t in a})==1
    assert len({t['entry_source_hash'] for t in a})==1
    assert all(t['exit_rule']=='structure_failure_atr' for t in r['trades'] if t['group']=='B')
    assert r['summary']['B']['breakout']['sample_count']>0
    assert r['folds'] and all(f['purge']==60 for f in r['folds'])
    assert r['summary']['A']['structure_failure_atr']['round_trips']==1
    assert r['summary']['A']['structure_failure_atr']['mean_net_price_return'] is not None
    b['panels'][0]['bars'][120]['execution']={}
    assert all(t['status']=='unknown' for t in compare(b)['trades'] if t['group']=='A')


def test_scope_archive_idempotency_cross_week_and_corruption(tmp_path):
    from mt1.timing_cli import file_hash
    from mt1.longitudinal import weekly_index
    p=fixture()
    scope=tmp_path/'scope.json';scope.write_text(json.dumps({'scope_epoch':'test','reset_at':'2024-01-01T00:00:00Z',
        'confirmed_holdings':[{'code':'TEST'}],'active_candidates':[]}))
    b={'source_kind':'SYNTHETIC','scope_epoch':'test','scope_codes':['TEST'],'asof':ASOF,'panels':[p],'inputs':[], 'contract_hash':digest(contract())}
    path=tmp_path/'bundle.json';path.write_text(json.dumps(b))
    root=tmp_path/'archive'
    a=observe(path,scope,root);c=observe(path,scope,root)
    assert a['manifest']==c['manifest'] and c['idempotent']
    w=weekly_index(root,asof='2026-12-01',current_epoch='test')
    assert len(w['pending'])==1 and not w['integrity_errors']
    b['scope_codes'].append('OLD4714');path.write_text(json.dumps(b))
    with pytest.raises(ValueError,match='scope'):observe(path,scope,root)
    b['scope_codes']=['TEST'];path.write_text(json.dumps(b))
    manifest=json.loads(Path(a['manifest']).read_text()); rp=Path(a['manifest']).parent/'result.json';rp.write_text('{}')
    with pytest.raises(ValueError,match='corrupt'):observe(path,scope,root)


def test_9_holdings_no_cost_foreign_scope_blocked(tmp_path):
    from mt1.scope import load
    scope=tmp_path/'scope.json';scope.write_text(json.dumps({'scope_epoch':'test','reset_at':'2024-01-01T00:00:00Z',
        'confirmed_holdings':[{'code':str(i)} for i in range(9)],'active_candidates':[]}))
    assert len(load(scope)['holdings'])==9
    s=json.loads(scope.read_text());s['active_candidates']=[{'code':'legacy'}];scope.write_text(json.dumps(s))
    with pytest.raises(ValueError):load(scope)


def test_failure_episode_expires_and_never_means_any_ma_break():
    p=fixture(140);rows=normalize(p,ASOF)
    s,c=step(rows[:120],None,contract(),channel='VALUE',observed_at=ASOF)
    s['breakout_episode']={'date':rows[119]['date'],'session':-20,'level':500,'failed_closes':8,'risk':20}
    s,c=step(rows[:121],s,contract(),channel='VALUE',observed_at=ASOF)
    assert 'registered_breakout_failed' not in c['risk']['reasons']


def test_touch_high_without_setup_not_breakout():
    p=fixture()
    for b in p['bars'][-20:]: b['high']+=20
    r=evaluate(p,asof=ASOF)
    assert r['entry']['breakout']['status']=='watch_structure'
    p=append(p,140,200);r=evaluate(p,asof=ASOF,previous=r['state'])
    assert r['entry']['breakout']['status']!='trigger'


def test_prefix_future_change_cannot_change_past_state():
    p=fixture();first=evaluate(p,asof=ASOF)
    q=append(p,200,300)
    assert evaluate(p,asof=ASOF)==first
    assert evaluate(q,asof=ASOF,previous=first['state'])['state']['monitor']['origin_date']==p['bars'][-1]['date']


def test_cost_and_open_execution_not_signal_close():
    b=experiment_fixture();r=compare(b)
    t=next(t for t in r['trades'] if t['group']=='A' and t['arm']=='structure_failure_atr')
    assert t['entry_fill']['date']>t['signal_date'] and t['exit_fill']['date']>t['exit_signal_date']
    cost=contract()['execution']['per_side_cost_bps']['CN']/10000
    assert t['net_price_return']==pytest.approx(t['exit_fill']['price']*(1-cost)/(t['entry_fill']['price']*(1+cost))-1)


def test_overlapping_A_episodes_rejected():
    b=experiment_fixture();e=copy.deepcopy(b['entry_episodes'][0]);e['id']='second';e['signal_date']=b['panels'][0]['bars'][125]['date'];b['entry_episodes'].append(e)
    with pytest.raises(ValueError,match='overlapping'):compare(b)


def test_missing_prices_keep_three_market_coverage_unknown():
    b=experiment_fixture()
    b['scope_codes']=['TEST','HKTEST','USTEST']
    for market in ('HK','US'):
        p=fixture(market=market);p['code']=market+'TEST';p['adjustment']='unknown';b['panels'].append(p)
    r=compare(b)
    assert len(r['audit'])==3 and sum(a['status']=='blocked' for a in r['audit'])==2


def test_full_nine_holdings_archive_no_cost(tmp_path):
    scope=tmp_path/'scope.json';holdings=[{'code':str(i)} for i in range(9)]
    scope.write_text(json.dumps({'scope_epoch':'test','reset_at':'2024-01-01T00:00:00Z','confirmed_holdings':holdings,'active_candidates':[]}))
    panels=[]
    for i in range(9):
        p=fixture(market='CN' if i<6 else 'HK');p['code']=str(i)
        if i>=6: p['adjustment']='unknown'
        panels.append(p)
    b={'scope_epoch':'test','scope_codes':[str(i) for i in range(9)],'panels':panels,'inputs':[],'asof':ASOF,'contract_hash':digest(contract())}
    path=tmp_path/'bundle.json';path.write_text(json.dumps(b))
    receipt=observe(path,scope,tmp_path/'archive')
    r=json.loads((Path(receipt['manifest']).parent/'result.json').read_text())
    assert len(r['cards'])==9 and all(c['actual_cost'] is None and c['personal_pnl'] is None and c['holding_status']=='confirmed' for c in r['cards'])
    assert sum(c['status']=='blocked' for c in r['cards'])==3


def test_cross_week_previous_manifest_preserves_event_and_no_reset(tmp_path):
    scope=tmp_path/'scope.json';scope.write_text(json.dumps({'scope_epoch':'test','reset_at':'2024-01-01T00:00:00Z','confirmed_holdings':[{'code':'TEST'}],'active_candidates':[]}))
    p=fixture();b={'scope_epoch':'test','scope_codes':['TEST'],'panels':[p],'inputs':[],'asof':ASOF,'contract_hash':digest(contract())}
    path=tmp_path/'bundle.json';path.write_text(json.dumps(b));root=tmp_path/'archive'
    a=observe(path,scope,root)
    for i in range(8):p=append(p)
    b['panels']=[p];path.write_text(json.dumps(b))
    with pytest.raises(ValueError,match='previous_manifest_required'):observe(path,scope,root)
    a2=observe(path,scope,root,a['manifest'])
    m1=json.loads(Path(a['manifest']).read_text());m2=json.loads(Path(a2['manifest']).read_text())
    assert m1['events'][0]['entity_id']==m2['events'][0]['entity_id']
    assert m1['events'][0]['original_date']==m2['events'][0]['original_date']
    assert m2['events'][0]['observed_sessions']==9


def test_next_day_requires_calendar_refresh_not_cached_latest():
    p=fixture()
    assert evaluate(p,asof='2026-09-14T13:00:00+00:00')['status']=='blocked'


def test_forged_close_timestamp_cannot_hide_future_bar():
    p=fixture();p['bars'][-1]['close_at']=p['bars'][-2]['close_at']
    assert evaluate(p,asof=ASOF)['status']=='blocked'


def test_nonpositive_atr_price_is_unknown_not_negative_stop():
    p=fixture()
    for b in p['bars'][-21:]:b.update(low=1,high=250)
    r=evaluate(p,asof=ASOF)
    assert r['atr_stop'] is None and 'ATR_stop_nonpositive_unusable' in r['gaps']


def test_same_session_auxiliary_can_refresh_without_extra_observation():
    p=fixture();r=evaluate(p,asof=ASOF)
    p['benchmarks']['industry']={**copy.deepcopy(p['benchmarks']['broad']),'id':'IND','membership_basis':'test','membership_known_at':ASOF}
    r2=evaluate(p,asof=ASOF,previous=r['state'])
    assert r2['rs']['industry']['status']=='ok'
    assert r2['state']['observed_sessions']==1


def window_panel(master, end, width=120):
    p=copy.deepcopy(master)
    p['bars']=p['bars'][max(0,end-width):end]
    p['sessions']=[b['date'] for b in p['bars']]
    p['expected_date']=p['sessions'][-1]
    return p


def test_horizon_outcomes_survive_rolling_120_and_idempotency():
    master=fixture(265)
    result=None; frozen=None
    for end in range(120,266):
        p=window_panel(master,end)
        result=evaluate(p,asof=ASOF,previous=result['state'] if result else None)
        assert result['status']=='ok'
        if end==180:
            frozen=copy.deepcopy(result['horizons'])
            assert all(h['status']=='observed_price_only' for h in frozen)
        if end>180:
            assert result['horizons']==frozen
    assert result['state']['monitor']['origin_date'] not in p['sessions']
    assert result['state']['observed_sessions']==146
    for h in frozen:
        assert h['target_date']==master['bars'][119+h['sessions']]['date']
        assert h['source_bar_sha256']==digest(master['bars'][119+h['sessions']])
        assert h['source_panel_sha256']
    again=evaluate(p,asof=ASOF,previous=result['state'])
    assert again['horizons']==frozen and again['state']['observed_sessions']==146


def test_partial_and_blocked_horizons_are_not_false_completion():
    from mt1.timing_cli import card_horizons, horizon_status, report
    master=fixture(141)
    a=evaluate(window_panel(master,120),asof=ASOF)
    b=evaluate(window_panel(master,141,width=141),asof=ASOF,previous=a['state'])
    assert [h['status'] for h in b['horizons']]==['observed_price_only','not_matured','not_matured']
    assert horizon_status(card_horizons(b))=='not_matured'
    assert '20交易日=observed_price_only' in report({'cards':[b]})
    bad=window_panel(master,141); bad['adjustment']='unknown'
    c=evaluate(bad,asof=ASOF,previous=b['state'])
    assert [h['status'] for h in card_horizons(c)]==['observed_price_only','blocked','blocked']
    assert horizon_status(card_horizons(c))=='blocked'
    assert '20交易日=observed_price_only' in report({'cards':[c]})


def test_archive_to_weekly_maturity_and_rolling_preservation(tmp_path, monkeypatch):
    import mt1.timing_cli as cli
    from datetime import datetime
    from mt1.longitudinal import weekly_index
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):
            return datetime.fromisoformat(ASOF).astimezone(tz)
    monkeypatch.setattr(cli,'datetime',Clock)
    master=fixture(265)
    scope=tmp_path/'scope.json'; scope.write_text(json.dumps({'scope_epoch':'test','reset_at':'2024-01-01T00:00:00Z',
        'confirmed_holdings':[{'code':'TEST'}],'active_candidates':[]}))
    root=tmp_path/'archive'; prior=None; mature=None
    # Daily 120-bar rolling inputs, including >120 continuation sessions.
    for end in range(120,266):
        bundle={'source_kind':'SYNTHETIC','scope_epoch':'test','scope_codes':['TEST'],'asof':ASOF,
            'panels':[window_panel(master,end)],'inputs':[],'contract_hash':digest(contract())}
        path=tmp_path/f'bundle-{end}.json'; path.write_text(json.dumps(bundle))
        receipt=observe(path,scope,root,prior)
        prior=receipt['manifest']; m=json.loads(Path(prior).read_text())
        result=json.loads((Path(prior).parent/m['result']['path']).read_text())
        event=m['events'][0]
        if end==140:
            assert [h['status'] for h in event['horizons']]==['observed_price_only','not_matured','not_matured']
            assert event['status']=='not_matured'
        if end>=180:
            assert event['status']=='archived'
            assert 'price_horizons_pending_or_blocked' not in result['incomplete']
            if mature is None: mature=copy.deepcopy(event['horizons'])
            assert event['horizons']==mature
    for cutoff in ('2026-09-18','2026-12-01'):
        w=weekly_index(root,asof=cutoff,current_epoch='test')
        assert not w['pending'] and not w['integrity_errors']
        assert w['events'][0]['horizons']==mature
        assert w['events'][0]['status']=='archived'
    repeated=observe(path,scope,root,str(Path(prior)))
    # Same-day new provenance cannot reset the monitor or change matured values.
    m=json.loads(Path(repeated['manifest']).read_text())
    assert m['events'][0]['horizons']==mature
