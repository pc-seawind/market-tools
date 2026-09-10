"""Isolated synthetic maturity tests. No live returns manufactured."""
import copy
import json
from datetime import datetime,date,timedelta,timezone
from pathlib import Path
import pytest
from mt1.forward import CONTRACT,schedule,harvest,harvest_id,register,source_clearance


def setup(tmp_path):
    dates=[date(2026,1,1)+timedelta(days=i) for i in range(180)]
    rows=[{'cal_date':d.strftime('%Y%m%d'),'is_open':'1' if d.weekday()<5 else '0'} for d in dates]
    # Synthetic holiday, explicitly not weekday substitution.
    rows[5]['is_open']='0'
    cal={'exchange':'SSE','fetched_at':'2026-04-01T07:00:00+08:00','rows':rows}
    observation='2026-04-01T16:00:00+08:00'
    sch=schedule('20260102','2026-01-02T16:00:00+08:00',cal,observation)
    co={'cohort_id':'synthetic-only','price_asof':'20260102','decision_at':'2026-01-02T16:00:00+08:00','contract':copy.deepcopy(CONTRACT),'schedule':sch,'members':[{'code':'x'},{'code':'y'}]}
    days=[r['cal_date'] for r in rows if r['is_open']=='1' and r['cal_date']<='20260401']
    from hashlib import sha256
    src=tmp_path/'synthetic-evidence';src.write_text('SYNTHETIC NO ACTION STATUS')
    clearance=dict(start_date=sch['entry_date'],end_date='20260401',checked_at='2026-04-01T15:30:00+08:00',listing_status='listed',actions_status='none_verified',suspension_status='none_verified',sources=[{'url':'https://example.com','published_at':'2026-04-01T15:00:00+08:00','path':str(src),'sha256':sha256(src.read_bytes()).hexdigest()}])
    data={'daily':{},'factors':{},'benchmark':[dict(trade_date=d,open=100,close=105) for d in days],'clearances':{}}
    for code in ('x','y'):
        data['daily'][code]=[dict(ts_code=code,trade_date=d,open=100,close=110,vol=100) for d in days]
        data['factors'][code]=[dict(ts_code=code,trade_date=d,adj_factor=1) for d in days]
        data['clearances'][code]={'code':code,**clearance}
    return co,cal,observation,data


def test_mature_synthetic_cost_benchmark_cash_contract(tmp_path):
    co,cal,now,data=setup(tmp_path)
    out=harvest(co,cal,now,data)
    r=next(r for r in out['rows'] if r['horizon']==20)
    assert r['status']=='observed_diagnostic'
    assert r['gross_return']==pytest.approx(.1)
    assert r['net_return']==pytest.approx(110*.999/(100*1.001)-1)
    assert r['benchmark_return']==pytest.approx(.05)
    assert not out['strategy_validated'] and 'no reinvestment' in out['contract']['capital']


def test_first_and_maturity_trading_dates_exclude_holiday(tmp_path):
    co,cal,now,data=setup(tmp_path)
    assert co['schedule']['entry_date']=='20260105'
    days=[r['cal_date'] for r in cal['rows'] if r['is_open']=='1']
    idx=days.index('20260105')
    assert co['schedule']['maturity_dates']['20']==days[idx+20]


def test_not_matured_has_no_returns_even_with_synthetic_prices(tmp_path):
    co,cal,now,data=setup(tmp_path)
    now='2026-01-05T16:00:00+08:00';cal['fetched_at']='2026-01-05T15:00:00+08:00'
    result=harvest(co,cal,now,data)
    assert all(r['status']=='not_matured' and r['net_return'] is None for r in result['rows'])


def test_maturity_close_exact_boundary(tmp_path):
    co,cal,now,data=setup(tmp_path);d=co['schedule']['maturity_dates']['20'];iso=f'{d[:4]}-{d[4:6]}-{d[6:]}'
    cal['fetched_at']=iso+'T08:00:00+08:00'
    before=harvest(co,cal,iso+'T14:59:59.999999+08:00')
    at=harvest(co,cal,iso+'T15:00:00+08:00')
    assert before['rows'][0]['status']=='not_matured'
    assert at['rows'][0]['status']=='blocked' # mature but no actual panel


@pytest.mark.parametrize('kind',['missing','suspension','delisted','corporate','factor','unknown_actions','benchmark','foreign','duplicate','future','hash'])
def test_unknown_or_untradable_blocks_not_zero_returns(tmp_path,kind):
    co,cal,now,data=setup(tmp_path);end=co['schedule']['maturity_dates']['20'];entry=co['schedule']['entry_date']
    if kind=='missing':data['daily']['x']=[r for r in data['daily']['x'] if r['trade_date']!=end]
    if kind=='suspension':data['clearances']['x']['suspension_status']='suspended'
    if kind=='delisted':data['clearances']['x']['listing_status']='delisted'
    if kind=='corporate':data['clearances']['x']['actions_status']='cash_dividend'
    if kind=='factor':next(r for r in data['factors']['x'] if r['trade_date']==end)['adj_factor']=2
    if kind=='unknown_actions':del data['clearances']['x']
    if kind=='benchmark':data['benchmark']=[r for r in data['benchmark'] if r['trade_date']!=end]
    if kind=='foreign':next(r for r in data['daily']['x'] if r['trade_date']==end)['ts_code']='z'
    if kind=='duplicate':data['daily']['x'].append(copy.deepcopy(data['daily']['x'][0]))
    if kind=='future':data['daily']['x'].append(dict(ts_code='x',trade_date='20260402',open=100,close=110,vol=100))
    if kind=='hash':data['clearances']['x']['sources'][0]['sha256']='bad'
    result=harvest(co,cal,now,data);r=next(r for r in result['rows'] if r['code']=='x' and r['horizon']==20)
    assert r['status']=='blocked' and r['net_return'] is None
    assert result['aggregates']['20']['equal_weight_net_return'] is None # don't drop failed constituent


def test_calendar_revision_does_not_move_frozen_deadline(tmp_path):
    co,cal,now,data=setup(tmp_path);original=copy.deepcopy(co['schedule'])
    cal['rows'][7]['is_open']='0'
    assert all(r['status']=='blocked' for r in harvest(co,cal,now,data)['rows'])
    assert co['schedule']==original


def test_calendar_hole_unknown_timezone_future(tmp_path):
    co,cal,now,data=setup(tmp_path);cal['rows'].pop(2)
    with pytest.raises(ValueError):harvest(co,cal,now,data)
    co,cal,now,data=setup(tmp_path);cal['fetched_at']='2026-04-02T00:00:00+08:00'
    with pytest.raises(ValueError):harvest(co,cal,now,data)


def test_intraday_rerun_idempotent_but_revised_input_new_artifact(tmp_path):
    co,cal,now,data=setup(tmp_path)
    first=harvest_id(co,cal,now,data)
    assert first==harvest_id(co,cal,'2026-04-01T20:00:00+08:00',data)
    data['daily']['x'][0]['close']=101
    assert first!=harvest_id(co,cal,now,data)


def test_cohort_registration_dedup_and_frozen_decision(tmp_path):
    import gzip,hashlib
    co,cal,now,data=setup(tmp_path);source=tmp_path/'source';source.mkdir()
    rows=[dict(code='x',name='SYNTHETIC',channels=['VALUE','TREND'],risk={'status':'unknown'},timing={'status':'wait'},technical={'close':100})]
    with gzip.open(source/'funnel.json.gz','wt') as f:json.dump(rows,f)
    h=hashlib.sha256((source/'funnel.json.gz').read_bytes()).hexdigest()
    s={'asof':'20260102','decision_at':'2026-01-02T16:00:00+08:00','method':{'id':'synthetic'},'code_hashes':{},'frozen_hashes':{'funnel.json.gz':h}}
    (source/'summary.json').write_text(json.dumps(s));root=tmp_path/'registry'
    cal['fetched_at']='2026-01-03T07:00:00+08:00';now='2026-01-03T08:00:00+08:00'
    a=register(root,source,cal,now)
    s['decision_at']='2026-01-03T08:00:00+08:00';(source/'summary.json').write_text(json.dumps(s))
    b=register(root,source,cal,now)
    assert a==b and len(list((root/'cohorts').glob('*.json')))==1
    assert a['decision_at']=='2026-01-02T16:00:00+08:00'
    late=copy.deepcopy(cal);late['fetched_at']='2026-04-01T07:00:00+08:00'
    with pytest.raises(ValueError,match='late_registration'):
        register(tmp_path/'late-registry',source,late,'2026-04-01T16:00:00+08:00')


def test_same_day_evening_benchmark_does_not_reuse_morning(tmp_path,monkeypatch):
    import mt1.parallel_collect as module
    now=[datetime.fromisoformat('2026-09-11T08:00:00+08:00')]
    class Clock:
        @classmethod
        def now(cls,tz=None):return now[0].astimezone(tz) if tz else now[0]
    calls=[]
    def api(name,**params):
        calls.append((name,params))
        if name=='trade_cal':
            return [{'cal_date':(date(2026,2,1)+timedelta(days=i)).strftime('%Y%m%d'),'is_open':'1'} for i in range(223)]
        return [{'synthetic_only':True}]
    monkeypatch.setattr(module,'datetime',Clock);monkeypatch.setattr(module,'api',api)
    module.collect(tmp_path)
    now[0]=datetime.fromisoformat('2026-09-11T16:00:00+08:00');module.collect(tmp_path)
    assert {p['end_date'] for n,p in calls if n=='index_daily'}=={'20260910','20260911'}
    assert (tmp_path/'index_daily-20260910.json').exists() and (tmp_path/'index_daily-20260911.json').exists()
