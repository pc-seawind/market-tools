"""Synthetic boundary fixtures, never mixed into live artifacts."""
import copy
from datetime import date,timedelta
import pytest
from mt1.parallel import financial,discover,risks,features,timing,actions,board,METHOD


def fin():
    return financial([{'end_date':'20260630','ann_date':'20260801','roe':'5','debt_to_assets':'40','netprofit_yoy':'100'},
                      {'end_date':'20251231','ann_date':'20260301','roe':'12'}],'20260910')


def tech():
    return dict(asof='20260910',close=105,ma20=100,ma60=90,ma60_5ago=88,return60=.2,rs60=.1,
        extension20=.05,drawdown120=-.25,previous_high20=104,volume_ratio=1.3,today_low=103,atr20=2,below_ma60_5sessions=False)


@pytest.mark.parametrize('pe',[26,100,1000,-10,None])
def test_trend_not_value_gate(pe):
    f=fin();d={'pe_ttm':pe,'pb':20}
    assert discover('TREND',f,d,tech())['status']=='pass'
    assert discover('VALUE',f,d,tech())['status']!='pass'
    assert actions(tech(),timing(tech(),'20260910','20260910'),'20260910')['final_buy'] is False


def test_halfyear_not_annual_and_no_multiply():
    f=fin();assert f['current']['roe']=='5';assert f['annual']['roe']=='12'
    assert discover('VALUE',f,{'pe_ttm':10,'pb':1},None)['status']=='pass'
    f['annual']=None
    assert discover('VALUE',f,{'pe_ttm':10,'pb':1},None)['status']=='unknown'


@pytest.mark.parametrize('rows',[[{'end_date':'20260630','ann_date':'20260910'}],[{'end_date':'20260630','ann_date':'20260911'}],[{'end_date':'20261231','ann_date':'20260801'}],[{'end_date':'20241231','ann_date':'20250301'}],[]])
def test_future_same_day_stale_unknown_financials(rows):
    assert financial(rows,'20260910')['current'] is None


def test_conflicting_latest_financial():
    a={'end_date':'20260630','ann_date':'20260801','roe':'5'}
    assert financial([a,{**a,'roe':'6'}],'20260910')['current'] is None


@pytest.mark.parametrize('industry,expected',[('银行','unknown'),('保险','unknown'),('证券','unknown'),('多元金融','unknown'),('半导体','reject'),('', 'unknown')])
def test_industry_adaptation(industry,expected):
    f=fin();f['current']['debt_to_assets']='85'
    assert risks({'name':'样例','industry':industry},{'trade_date':'20260910','turnover_rate':1},f,'20260910')['status']==expected


@pytest.mark.parametrize('actual,expected',[('20260909','20260910'),('20260911','20260910'),('20260910','20260911')])
def test_stale_future_timing(actual,expected):
    assert timing(tech(),actual,expected)['status']=='unknown'


def test_trigger_cancel_hold_separation_and_exit_priority():
    t=tech();tm=timing(t,'20260910','20260910');assert tm['status']=='trigger'
    t['extension20']=.2
    tm=timing(t,'20260910','20260910');assert tm['status']=='wait'
    p=actions(t,tm,'20260910');assert p['unheld']=='研究未完成' and p['held_hypothetical']=='持有复核'
    p=actions(None,{'status':'unknown'},'20260910','thesis_invalidated')
    assert p['held_hypothetical']=='退出复核' and p['qualification']!='final'


def test_pullback_trigger_volume_cancel():
    t=tech();t.update(previous_high20=110,today_low=100,volume_ratio=1.0)
    assert timing(t,'20260910','20260910')['reasons']==['near_MA20_low_volume']
    t['volume_ratio']=1.15
    assert timing(t,'20260910','20260910')['status']=='wait'


def panel():
    # Synthetic dates are sufficient for pure join tests; live calendar is provider checked.
    days=[(date(2026,1,1)+timedelta(days=i)).strftime('%Y%m%d') for i in range(120)]
    bars=[dict(ts_code='x',trade_date=d,close=100+i,high=102+i,low=99+i,vol=100) for i,d in enumerate(days)]
    factors=[dict(ts_code='x',trade_date=d,adj_factor=1) for d in days]
    bench=[dict(trade_date=d,close=100) for d in days]
    return days,bars,factors,bench


def test_actual_features_and_adjustment():
    d,b,f,i=panel();t=features('x',d,b,f,i)
    assert t['close']==219 and t['warmup_sessions']==120
    assert t['rs60']==t['return60']
    assert t['atr20']==3


@pytest.mark.parametrize('kind',['warmup','hole','duplicate','future','factor','volume','benchmark','nan'])
def test_bad_panels_fail_closed(kind):
    d,b,f,i=panel()
    if kind=='warmup':d=d[1:]
    if kind=='hole':b.pop(50)
    if kind=='duplicate':b.append(b[0])
    if kind=='future':b[-1]['trade_date']='20270101'
    if kind=='factor':f[-1]['adj_factor']=0
    if kind=='volume':b[-1]['vol']=0
    if kind=='benchmark':i.pop()
    if kind=='nan':b[-1]['close']='nan'
    with pytest.raises((ValueError,KeyError)):features('x',d,b,f,i)


def test_reversal_not_profit_gate():
    f=fin();f['current']['netprofit_yoy']='-100'
    assert discover('REVERSAL',f,{},tech())['status']=='pass'
    assert actions(tech(),timing(tech(),'20260910','20260910'),'20260910')['qualification']=='pending_review'


@pytest.mark.parametrize('code,b',[('688001.SH','科创'),('300001.SZ','创业'),('920001.BJ','北交'),('600000.SH','主板')])
def test_board_no_admission_whitelist(code,b):assert board(code)==b


def test_shadow_cannot_be_laundered_as_active():
    from test_mt1 import final_plan,method_lookup
    from mt1.plans import eligible
    p=final_plan();p['candidate_origin']='mt11_parallel_shadow'
    assert not eligible(p,date.today(),method_lookup)
    p=final_plan();p['method_status']='shadow'
    assert not eligible(p,date.today(),method_lookup)


@pytest.mark.parametrize('change',['future','expired','hash','code','valuation','none','pass'])
def test_research_packet_validation(tmp_path,change):
    import hashlib
    from mt1.parallel import review_gate
    path=tmp_path/'evidence';path.write_text('SYNTHETIC TEST ONLY')
    p=dict(code='x',channel='TREND',kind='valuation',reviewer='tester',reviewed_at='20260909',valid_until='20260920',conclusion='pass',reason='synthetic',sources=[dict(published_at='20260908',url='https://example.com',path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest())],basis='earnings_scenarios',assumptions='synthetic',downside_case='synthetic',price_below=100)
    if change=='future':p['sources'][0]['published_at']='20260911'
    if change=='expired':p['valid_until']='20260909'
    if change=='hash':p['sources'][0]['sha256']='bad'
    if change=='code':p['code']='y'
    if change=='valuation':p['price_below']=None
    if change=='none':p=None
    assert review_gate(p,'x','TREND','20260910','valuation')['status']==('pass' if change=='pass' else 'pending' if change=='none' else 'unknown')


def test_full_funnel_conservation_and_frozen_hashes(tmp_path):
    import json,hashlib,gzip
    from datetime import datetime,timezone
    from zoneinfo import ZoneInfo
    from mt1.parallel import run_parallel
    now=datetime.now(timezone.utc);local=now.astimezone(ZoneInfo('Asia/Shanghai'))
    end=local.date() if local.hour>=15 else local.date()-timedelta(days=1)
    days=[(end-timedelta(days=119-i)).strftime('%Y%m%d') for i in range(120)]
    root=tmp_path/'panel';root.mkdir();state=tmp_path/'state';sweep=state/'sweeps'/str(end);sweep.mkdir(parents=True)
    def save(p,obj):p.write_text(json.dumps(obj))
    def env(api,rows,**params):return dict(api=api,rows=rows,params=params,fetched_at=now.isoformat())
    save(root/'sessions.json',days)
    save(root/'trade_cal-all.json',env('trade_cal',[{'cal_date':d,'is_open':'1'} for d in days]+([] if days[-1]==local.strftime('%Y%m%d') else [{'cal_date':local.strftime('%Y%m%d'),'is_open':'1'}]),end_date=local.strftime('%Y%m%d')))
    codes=['600001.SH','688001.SH','300001.SZ','920001.BJ']
    save(sweep/'stock_basic.json',[dict(ts_code=c,name='SYNTHETIC',industry='半导体') for c in codes])
    save(sweep/'daily_basic.json',[dict(ts_code=c,trade_date=days[-1],pe_ttm=100,pb=8,turnover_rate=1) for c in codes])
    for c in codes:
        save(sweep/('financial-'+c+'.json'),[dict(ts_code=c,end_date=str(end.year-1)+'1231',ann_date=str(end.year)+'0301',roe=12,debt_to_assets=30,netprofit_yoy=30)])
    for i,d in enumerate(days):
        save(root/('daily-'+d+'.json'),env('daily',[dict(ts_code=c,trade_date=d,close=100+i,high=101+i,low=99+i,vol=100) for c in codes],trade_date=d))
        save(root/('adj_factor-'+d+'.json'),env('adj_factor',[dict(ts_code=c,trade_date=d,adj_factor=1) for c in codes],trade_date=d))
    save(root/'index_daily-all.json',env('index_daily',[dict(trade_date=d,close=100) for d in days]))
    out=tmp_path/'result';result=run_parallel(state,root,out)
    summary=json.loads((out/'summary.json').read_text())
    assert result['universe']==4 and result['deduplicated']==4
    for channel in summary['channels'].values():assert sum(channel['discovery'].values())==4
    with gzip.open(out/'funnel.json.gz','rt') as fp:records=json.load(fp)
    assert len(records)==4 and all(r['channels']==['TREND'] for r in records)
    assert all(r['plan']['final_buy'] is False for r in records)
    for path,h in summary['frozen_hashes'].items():assert hashlib.sha256((out/path).read_bytes()).hexdigest()==h
    from scripts.audit_mt11_artifact import audit
    assert audit(out)['stage_rows']==12
    with pytest.raises(FileExistsError):run_parallel(state,root,out)
    (out/'financial-input.json.gz').write_bytes(b'tampered synthetic file')
    with pytest.raises(AssertionError):audit(out)


def test_growth_negative_base_not_quality_bonus():
    rows=[dict(end_date='20260630',ann_date='20260801',eps=1,netprofit_yoy=100),dict(end_date='20250630',ann_date='20250801',eps=-1)]
    assert not financial(rows,'20260910')['positive_eps_base']
    rows[1]['eps']=.5
    f=financial(rows,'20260910')
    assert f['positive_eps_base'] and f['current_period_type']=='cumulative_interim_not_TTM'
