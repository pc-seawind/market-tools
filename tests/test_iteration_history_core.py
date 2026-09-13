"""Synthetic correctness only. Original screen/step/decide are NOT mocked."""
import copy
import pytest
from mt1.iteration_history import price_value,cash,unknown,historical_action,execution_value
from mt1.iteration_history_report import stats,summarize
from mt1.iteration_history_data import write,read,sha,verify_inputs
from mt1.action_loop import decide,read as policy_read,POLICY
from mt1.action_demo import fixture,append
from mt1.timing import normalize
from mt1.candidates import screen


def panel(n=65):
    return dict(bars=[dict(date=str(i),close=100+i,open=100+i) for i in range(n)],dates=[str(i) for i in range(n)])


def snapshot():
    return dict(asof='20220131',universe_size=1,data_version='SYNTHETIC',observations=[dict(stock=dict(ts_code='TEST',name='DEMO'),daily=dict(trade_date='20220131',pe_ttm=15,pb=2,turnover_rate=1),financials=[dict(ann_date='20211201',end_date='20210930',roe=11,ocfps=1,eps=1,debt_to_assets=40)])])


def test_screen_default_and_single_roe_difference():
    s=snapshot();assert screen(s)==screen(s,dict(roe_min=10,pe_max=25,pb_max=3))
    assert len(screen(s)['candidates'])==1 and not screen(s,{'roe_min':12})['candidates']


@pytest.mark.parametrize('ann',['20220131','20220201'])
def test_future_or_same_day_announcement_not_available(ann):
    s=snapshot();s['observations'][0]['financials'][0]['ann_date']=ann
    assert not screen(s)['candidates']


@pytest.mark.parametrize('field,value',[('ocfps',0),('eps',0),('debt_to_assets',80)])
def test_other_financial_filters_preserved(field,value):
    s=snapshot();s['observations'][0]['financials'][0][field]=value
    assert not screen(s)['candidates']


def test_next_close_and_exact_horizon():
    p=panel();v=price_value(p,0,20,.0015)
    assert v['entry_date']=='1' and v['end_date']=='21'
    assert v['net']==pytest.approx(121*.9985/(101*1.0015)-1)
    assert v['gross']==pytest.approx(121/101-1)


def test_maturity_not_zero():
    assert price_value(panel(20),0,20,0)['net'] is None


def test_hole_not_removed_from_path():
    p=panel();p['bars'][10]=None
    assert price_value(p,0,20,0)['net'] is None


def test_fees_monotonically_reduce_value():
    vals=[price_value(panel(),0,20,.0025*m)['net'] for m in (1,2,3)]
    assert vals[0]>vals[1]>vals[2]


def test_loss_is_retained_and_drawdown_measured():
    p=panel();p['bars'][21]['close']=80
    v=price_value(p,0,20,.0015);assert v['net']<0 and v['drawdown']<v['net']


def test_cash_unknown_common_denominator():
    s=stats([cash(),unknown('unknown')]);assert s['total']==2 and s['known']==1 and s['full_denominator_mean'] is None


def test_open_mark_does_not_fake_exit_cost():
    p=panel();t=dict(status='open',entry=dict(index=1,price=101),exit=None)
    r=execution_value(p,t,20,.0025);assert r['status']=='open_mark_not_closed'
    assert r['net']==pytest.approx(121/(101*1.0025)-1)
    assert r['hypothetical_sell_cost_net']<r['net']


def test_exit_before_horizon_then_cash():
    p=panel();t=dict(status='closed',entry=dict(index=1,price=101),exit=dict(index=4,price=104))
    r=execution_value(p,t,20,.0025)
    assert r['net']==pytest.approx(104*.9975/(101*1.0025)-1)


def test_unknown_execution_not_cash():
    t=dict(status='unknown_execution',entry=None)
    assert execution_value(panel(),t,20,0)['net'] is None


def test_immutable_write_and_source_tamper(tmp_path):
    write(tmp_path/'data.json',{'x':1});write(tmp_path/'data.json',{'x':1})
    with pytest.raises(ValueError):write(tmp_path/'data.json',{'x':2})
    write(tmp_path/'source-index.json',[])
    write(tmp_path/'input-manifest.json',{'data.json':sha(tmp_path/'data.json')})
    verify_inputs(tmp_path);(tmp_path/'data.json').write_text('{}')
    with pytest.raises(ValueError):verify_inputs(tmp_path)


@pytest.mark.parametrize('ratio',[1.2,1.4])
def test_pure_historical_actions_equal_unmodified_live_decide(ratio):
    cfg=policy_read(POLICY);cfg['timing']['breakout_volume_min']=ratio
    p=fixture();hist=None;live=None;held=False
    for price,vol in [(None,None),(108,200),(110,160),(80,100),(82,90),(115,200)]:
        if price is not None:p=append(p,price,vol)
        p['fetched_at']=p['bars'][-1]['close_at']
        rows=normalize(p,p['fetched_at'])
        hist,r,action=historical_action(rows,hist,held,cfg['timing'])
        rr,card=decide(p,p['fetched_at'],live,held,cfg)
        # Unheld episode reset belongs to MT13 observe, rather than decide.
        if not held and live and card['risk_active'] and not rr['risk']['reasons'] and p['expected_date']>live['last_date']:
            rr,card=decide(p,p['fetched_at'],None,False,cfg)
        assert rr['status']=='ok' and action==card['action']
        assert r['entry']==rr['entry'] and r['risk']==rr['risk']
        live=rr['state']
        if action=='BUY':held=True
        if action=='SELL':held=False;hist=None;live=None


def test_baseline_frozen_policy_has_only_authorized_change():
    from pathlib import Path
    cfg=read(Path('reports/mt14-history-20260913/frozen-contract.json'))
    old=copy.deepcopy(cfg['technical']['old']);old['timing']['breakout_volume_min']=1.4
    assert old==cfg['technical']['new']
    assert cfg['technical']['old']==policy_read(POLICY)
    old=copy.deepcopy(cfg['fundamental']['old']);old['roe_min']=12
    assert old==cfg['fundamental']['new']


def test_missing_entry_dependencies_do_not_veto_frozen_sell():
    from mt1.iteration_history import frozen_exit_action
    state={'monitor':{'structure_low':90,'atr_stop':95,'risk_trigger':None}}
    action,_=frozen_exit_action({'close':94},state,True)
    assert action=='SELL'
    assert frozen_exit_action({'close':96},state,True)[0]=='DATA_BLOCKED'
    assert frozen_exit_action({'close':94},state,False)[0]=='WAIT'


def test_unknown_after_entry_kept_unknown_even_with_endpoint():
    t=dict(status='open',entry=dict(index=1,price=101),exit=None,unknown_index=8)
    assert execution_value(panel(),t,20,0)['net'] is None


def replay_fixture(suspended=()):
    from mt1.iteration_history import technical
    p=fixture()
    for close,vol in [(106,100),(108,200),(109,100),(80,100),(79,100),(78,100),(77,100),(76,100),(75,100),(74,100)]:
        p=append(p,close,vol)
    for i,b in enumerate(p['bars']):
        b['raw']={k:b[k] for k in ('open','high','low','close')};b['date']=b['date'].replace('-','');b['index']=i
        b['open_at']=b['close_at'].replace('17:00','09:30')
    dates=[b['date'] for b in p['bars']]
    p.update(dates=dates,sources=[],evidence=[dict(date=d,suspension=[{'suspend_type':'S'}] if i in suspended else [],limit=dict(up_limit=1000,down_limit=1),realization_at=p['bars'][i]['close_at']) for i,d in enumerate(dates)])
    old=policy_read(POLICY);new=copy.deepcopy(old);new['timing']['breakout_volume_min']=1.4
    cfg=dict(technical_codes=['SYNTH'],technical=dict(old=old,new=new),evaluation_start=dates[120],evaluation_end='20301231',horizons=[20,40,60],cost_multipliers=[1,2,3],per_side_bps={'CN':15},capital_slot=10000)
    return technical({'SYNTH':p},{},cfg),p


def test_historical_full_orders_buy_sell_next_open_not_same_close():
    (out,ds,trades,ledgers,accounts),p=replay_fixture()
    assert len(trades)==2
    for t in trades:
        assert t['status']=='closed'
        assert t['entry']['index']>t['signal_index']
        assert t['exit']['date']>t['entry']['date']
    assert len(accounts)==6
    assert any(x['status']=='occupied' for x in accounts[0]['path'])
    assert not any(r['status']=='unselected_cash' and r['date']==p['dates'][122] and r['category']=='technical_execution' for r in out)


def test_sell_expiry_uses_original_recovery_same_signal():
    (out,ds,trades,ledgers,accounts),p=replay_fixture(suspended=(124,125,126))
    for book in ledgers:
        sells=[o for o in book['orders'].values() if o['side']=='SELL']
        assert len(sells)==1
        sell=sells[0];assert len(sell['attempts'])==2
        assert sell['execution_status']=='filled'
        assert sell['fill']['date']>sell['attempts'][1]['after_session']
        assert any(t['to']=='expired' for t in book['transitions'])


def test_funded_capital_fees_cash_and_no_double_allocation():
    from mt1.iteration_history import account_path
    p=panel(10);trades=[dict(entry=dict(index=1,price=101),exit=dict(index=4,price=104))]
    path=account_path(p,trades,[],.0025,10000)
    assert path[0]['cash']==10000 and path[2]['cash']==0
    assert path[5]['wealth']==pytest.approx(10000*104*.9975/(101*1.0025))
    with pytest.raises(ValueError):account_path(p,trades+[dict(entry=dict(index=2,price=102),exit=None)],[],.0025,10000)
