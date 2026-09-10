import copy
from dataclasses import fields
import pytest
from mt1.historical import (universe, holes, next_open, recompute, rules_version,
                            select_version, envelope, instant)
from mt1.store import digest
from mt1.evidence import file_ref


def wrapped(tmp_path, payload, available='2024-01-02T16:00:00+08:00'):
    p = tmp_path / (digest(payload)+'.json')
    import json
    p.write_text(json.dumps(payload))
    return dict(payload=payload, payload_hash=digest(payload), available_at=available,
                observed_at=available, pit_verified=True, version_id='fixture-only',
                source=file_ref(p), availability_attestation=file_ref(p))


def bundle(tmp_path):
    from sector_picks import StockMetrics
    from etf_data import SectorSignals
    stock={f.name: 1.0 for f in fields(StockMetrics)}
    stock.update(code='600000.SH',name='fixture',trade_date='20240102',fina_as_of='20230930',
                 volume_signal='NORMAL',pv_alignment='中性',qfq_applied=True,
                 close=10, pe_ttm=10, roe=15, gross_margin=30, net_yoy=20, rev_yoy=20,
                 pe_median_3y=20, pct_rank_120d=20, dd_from_hi120=-45)
    sector={f.name:1.0 for f in fields(SectorSignals)}
    sector.update(concept='fixture',data_quality='direct',etfs=[])
    context=dict(sector_score_total=40,sector_roe_median=10,sector_margin_median=20,
                 peer_pe_median=20,min_deviation=20,sector_tier1_reason='c')
    data={'stock': stock,'sector':sector,'context':context,
          'financial':{k:stock[k] for k in ('roe','gross_margin','net_yoy','rev_yoy','fina_as_of')},
          'membership':{'codes':['600000.SH'],'concept':'fixture','in_date':'20200101','out_date':None}}
    return dict(decision_at='2024-01-02T17:00:00+08:00',rule_version=rules_version(),
                inputs={k:wrapped(tmp_path,v) for k,v in data.items()})


def test_real_evaluator_no_live_fetch(tmp_path,monkeypatch):
    import sector_picks
    monkeypatch.setattr(sector_picks,'_ts',lambda *a,**k:pytest.fail('live fetch'))
    r=recompute(bundle(tmp_path))
    assert r['status']=='recomputed_shadow',r
    assert (r['action'],r['channel'])==('BUY','REVERSAL')
    assert r['promotion']=='shadow' and not r['exact_backtest'] and r['metrics'] is None
    assert 'reason' not in r # no embedded unvalidated legacy return claims


@pytest.mark.parametrize('part',['stock','financial','sector','context','membership'])
@pytest.mark.parametrize('field,value', [('available_at','2024-01-03T00:00:00+08:00'),
    ('observed_at','2024-01-03T00:00:00+08:00'),('pit_verified',False),
    ('version_id',''),('available_at','2024-01-02T10:00:00')])
def test_future_and_unknown_all_inputs(tmp_path,part,field,value):
    b=bundle(tmp_path);b['inputs'][part][field]=value
    assert recompute(b)['status']=='blocked'


def test_changed_payload_source_rules_and_missing_fields(tmp_path):
    b=bundle(tmp_path);b['inputs']['stock']['payload']['roe']=90
    assert 'hash mismatch' in recompute(b)['blocker']
    b=bundle(tmp_path);b['rule_version']='old'
    assert 'rule version' in recompute(b)['blocker']
    b=bundle(tmp_path);del b['inputs']['stock']['payload']['dd_from_hi120']
    b['inputs']['stock']['payload_hash']=digest(b['inputs']['stock']['payload'])
    assert 'incomplete' in recompute(b)['blocker']
    b=bundle(tmp_path)
    from pathlib import Path
    Path(b['inputs']['financial']['source']['path']).write_text('modified')
    assert 'hash mismatch' in recompute(b)['blocker']


@pytest.mark.parametrize('part,field,value', [('stock','trade_date','20990101'),
    ('stock','fina_as_of','20990101'),('membership','in_date','20250101'),
    ('membership','out_date','20240102'),('financial','roe',99),('sector','data_quality','proxy')])
def test_effective_dates_and_cross_input_consistency(tmp_path,part,field,value):
    b=bundle(tmp_path);payload=copy.deepcopy(b['inputs'][part]['payload']);payload[field]=value
    b['inputs'][part]=wrapped(tmp_path,payload)
    assert recompute(b)['status']=='blocked'


def test_revision_selection_excludes_future_and_blocks_unknown(tmp_path):
    a=wrapped(tmp_path,{'roe':10});a['period']='20230930'
    b=wrapped(tmp_path,{'roe':20},'2024-02-01T12:00:00+08:00');b['period']='20230930'
    assert select_version([b,a],'2024-01-03T00:00:00+08:00')['payload']['roe']==10
    b['pit_verified']=False
    with pytest.raises(ValueError,match='PIT'): select_version([a,b],'2024-03-01T00:00:00+08:00')
    with pytest.raises(ValueError,match='ambiguous'): select_version([a,a],'2024-01-03T00:00:00+08:00')


def test_universe_ipo_delisting_and_invalid():
    rows=[dict(ts_code='A',list_date='20240102',delist_date='20240104',list_status='D'),
          dict(ts_code='B',list_date='20240103',list_status='L')]
    u=universe(rows,['2024-01-01','2024-01-02','2024-01-03','2024-01-04'])
    assert list(u['by_day'].values())==[[],['A'],['A','B'],['B']]
    assert not u['settlement_verified']
    rows[0]['delist_date']=''
    assert universe(rows,['2024-01-02'])['status']=='blocked'


def test_holes_no_suspension_inference_or_duplicate_rows():
    panel={'daily':[dict(ts_code='A',trade_date='20240102')], 'adj_factor':[],'stk_limit':[]}
    r=holes({'2024-01-02':['A','D']},panel,{})
    assert r['hole_count']==2 and r['holes'][0]['missing']==['adj_factor','stk_limit']
    assert r['holes'][1]['classification']=='suspension_coverage_unknown'
    r=holes({'2024-01-02':['D']},panel,{'2024-01-02':[]})
    assert r['holes'][0]['classification']=='unexplained_missing_row'
    panel['daily']*=2
    with pytest.raises(ValueError,match='duplicate'):holes({'2024-01-02':['A']},panel,{})


def fill_args():
    dates=['2024-01-02','2024-01-03','2024-01-04']
    return dict(code='A',decision_at='2024-01-02T15:01:00+08:00',side='buy',sessions=dates,
      daily=[dict(ts_code='A',trade_date=d,open=10,high=11,low=9,vol=100) for d in dates],
      limits=[dict(ts_code='A',trade_date=d,up_limit=11,down_limit=9) for d in dates],
      factors=[dict(ts_code='A',trade_date=d,adj_factor=2) for d in dates],
      suspension_days={d:[] for d in dates},fee_rate=.001,slippage_rate=.001)


def test_first_next_open_raw_costs_and_not_total_return():
    a=fill_args();r=next_open(**a)
    assert r['date']=='2024-01-03' and r['status']=='simulated_fill'
    assert r['cash_per_share']==pytest.approx(10*1.001*1.001)
    assert not r['replay_ready'] and not r['guaranteed_fill']
    a['side']='sell';assert next_open(**a)['cash_per_share']==pytest.approx(10*.999*.999)


@pytest.mark.parametrize('mode',['upper','lower','zero','suspend','slippage'])
def test_conservative_skip(mode):
    a=fill_args()
    if mode=='upper':a['daily'][1]['open']=11
    if mode=='lower':a['daily'][1]['open']=9
    if mode=='zero':a['daily'][1]['vol']=0
    if mode=='suspend':a['suspension_days']['2024-01-03']=[{'ts_code':'A','suspend_timing':'10:00-11:00'}]
    if mode=='slippage':a['limits'][1]['up_limit']=10.005
    r=next_open(**a);assert r['date']=='2024-01-04' and len(r['skipped'])==1


@pytest.mark.parametrize('mode',['missing','unknown','delist','nan','factor'])
def test_execution_missing_blocks_not_skips(mode):
    a=fill_args()
    if mode=='missing':a['daily'].pop(1)
    if mode=='unknown':a['suspension_days'].pop('2024-01-03')
    if mode=='delist':a['delist_date']='20240103'
    if mode=='nan':a['daily'][1]['open']=float('nan')
    if mode=='factor':a['factors'][1]['adj_factor']=0
    assert next_open(**a)['status']=='blocked'


def price_args():
    from datetime import date,timedelta
    ds=[str(date(2023,1,1)+timedelta(days=i)) for i in range(250)]
    return dict(code='A',decision_at=ds[-1]+'T16:00:00+08:00',sessions=ds,
      daily=[dict(ts_code='A',trade_date=d,close=10,vol=100) for d in ds],
      factors=[dict(ts_code='A',trade_date=d,adj_factor=1) for d in ds])


def test_raw_asof_adjustment_anchor():
    from mt1.historical import price_features
    a=price_args();a['factors'][-1]['adj_factor']=2
    r=price_features(**a)
    assert r['close']==10 and r['pct_1d']==100 and r['qfq_applied']


@pytest.mark.parametrize('mode',['future','hole','short','intraday','factor'])
def test_raw_features_refuse_lookahead_missing_and_short(mode):
    from mt1.historical import price_features
    a=price_args()
    if mode=='future':a['factors'].append(dict(ts_code='A',trade_date='20990101',adj_factor=100))
    if mode=='hole':a['daily'].pop(3)
    if mode=='short':a['sessions']=a['sessions'][-200:]
    if mode=='intraday':a['decision_at']=a['decision_at'].replace('16:00','10:00')
    if mode=='factor':a['factors'][10]['adj_factor']=0
    with pytest.raises(ValueError):price_features(**a)


def test_intraday_asof_cannot_use_full_day_features(tmp_path):
    b=bundle(tmp_path);b['decision_at']='2024-01-02T11:00:00+08:00'
    for part in b['inputs'].values():
        part['observed_at']=part['available_at']='2024-01-02T10:00:00+08:00'
    assert 'unclosed daily' in recompute(b)['blocker']


def test_observation_cannot_follow_availability(tmp_path):
    b=bundle(tmp_path);b['inputs']['stock']['observed_at']='2024-01-02T16:30:00+08:00'
    assert 'precedes observation' in recompute(b)['blocker']
