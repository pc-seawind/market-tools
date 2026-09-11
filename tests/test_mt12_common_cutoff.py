"""Synthetic accounting/unknown/OOS tests, separate from actual-price results."""
import copy
import pytest
from mt1.history_cutoff import value_event,summarize,compare_pairs,crossed_interval,drawdown


def fixture(closed=False,cost=15):
    dates=['20250102','20250103','20250106','20250107','20250108','20250109']
    bars=[dict(open=x,close=x) for x in [100,100,80,110,120,999]]
    p=dict(dates=dates,bars=bars)
    t=dict(code='TEST',fold=0,group='A',arm='old_ma60_5',scenario='test',cost_bps=cost,episode_id='e1',
        signal_date=dates[0],signal_index=0,status='closed' if closed else 'not_matured',entry_fill=dict(status='filled',index=1,date=dates[1],price=100))
    if closed:
        t.update(exit_fill=dict(status='filled',index=3,date=dates[3],price=110),exit_signal_date=dates[2],net_price_return=110*(1-cost/10000)/(100*(1+cost/10000))-1)
    return t,p,dict(test_start=dates[0],test_end=dates[4])


def test_unclosed_mark_exact_cutoff_not_future_no_sell_cost():
    t,p,f=fixture();r=value_event(t,p,f)
    assert r['entry_event_value']==pytest.approx(120/(100*1.0015)-1)
    assert r['realized_component']==0 and r['unrealized_component']==r['entry_event_value']
    assert r['actual_sell_fee_rate']==0 and r['sell_fee_paid_initial_capital']==0
    assert r['path'][-1]['date']==f['test_end'] and t['status']=='not_matured'


def test_closed_retains_cash_not_reinvested_at_later_price():
    t,p,f=fixture(True);r=value_event(t,p,f)
    assert r['entry_event_value']==t['net_price_return']
    assert r['path'][-1]['wealth']==r['path'][-2]['wealth']
    assert r['unrealized_component']==0


def test_full_path_includes_unclosed_adverse_excursion():
    t,p,f=fixture();r=value_event(t,p,f)
    assert r['full_path_drawdown']==pytest.approx(80/(100*1.0015)-1)
    assert r['full_path_drawdown']<-.2 and r['entry_event_value']>0


def test_closed_path_includes_losses_before_exit_and_cash_after():
    t,p,f=fixture(True);r=value_event(t,p,f)
    assert r['full_path_drawdown']==pytest.approx(80/(100*1.0015)-1)
    assert r['path'][-1]['kind']=='cash_after_exit'


def test_hypothetical_sell_fee_not_actual_fill():
    t,p,f=fixture();r=value_event(t,p,f)
    assert r['unpaid_sell_cost_sensitivity']==pytest.approx(1.2*(1-.0015)/(1+.0015)-1)
    assert 'exit_fill' not in t and r['exit_status']=='not_requested'


def test_closed_sensitivity_no_double_sell_cost():
    t,p,f=fixture(True);r=value_event(t,p,f)
    assert r['unpaid_sell_cost_sensitivity']==r['entry_event_value']


def test_fee_paid_amounts_not_percent_of_unfunded_price():
    t,p,f=fixture(True);r=value_event(t,p,f)
    assert r['buy_fee_paid_initial_capital']==pytest.approx(.0015/1.0015)
    assert r['sell_fee_paid_initial_capital']==pytest.approx(1.1*.0015/1.0015)


def test_missing_cutoff_not_last_available_fill():
    t,p,f=fixture();p['bars'][4]=None;r=value_event(t,p,f)
    assert r['valuation_status']=='unknown_cutoff_price'
    assert r['entry_event_value'] is None and r['full_path_drawdown'] is None
    assert r['observed_only_drawdown'] is not None


def test_exposed_internal_gap_invalidates_path_not_known_final_mark():
    t,p,f=fixture();p['bars'][2]=None;r=value_event(t,p,f)
    assert r['entry_event_value'] is not None and r['full_path_drawdown'] is None
    assert r['path_missing_dates']==[p['dates'][2]]


def test_missing_after_closed_does_not_invalidate_cash_path():
    t,p,f=fixture(True);p['bars'][4]=None;r=value_event(t,p,f)
    assert r['entry_event_value']==t['net_price_return'] and r['full_path_drawdown'] is not None


@pytest.mark.parametrize('status',['unknown','pending','cancelled'])
def test_unfilled_entry_distinguished_from_unknown(status):
    t,p,f=fixture();t['status']=status;t['entry_fill']=dict(status=status)
    r=value_event(t,p,f)
    assert r['entry_event_value'] is None
    assert r['capital_opportunity_value']==(None if status=='unknown' else 0)


def test_unknown_exit_not_assumed_still_held():
    t,p,f=fixture();t.update(status='unknown',exit_fill=dict(status='unknown'))
    r=value_event(t,p,f)
    assert r['entry_event_value'] is None and r['full_path_drawdown'] is None


@pytest.mark.parametrize('kind',['entry','exit','signal'])
def test_OOS_violation_rejected(kind):
    t,p,f=fixture(True)
    if kind=='entry':t['entry_fill']['index']=5
    elif kind=='exit':t['exit_fill']['index']=5
    else:t['signal_index']=5
    with pytest.raises(ValueError):value_event(t,p,f)


def test_unknown_retained_in_pair_and_summary_denominators():
    t,p,f=fixture();a=value_event(t,p,f);b=copy.deepcopy(a);b.update(arm='structure_failure_atr',entry_event_value=None,valuation_status='unknown_cutoff_price')
    s=summarize([a,b],'entry_event_value')
    assert (s['total'],s['known'],s['unknown'])==(2,1,1) and s['mean_over_full_denominator'] is None
    v=compare_pairs([a,b],'old_ma60_5','structure_failure_atr','A','entry_event_value')['valuation']
    assert (v['total'],v['known'],v['unknown'])==(1,0,1)


def test_A_mismatched_entries_not_attributed_to_exit():
    t,p,f=fixture();a=value_event(t,p,f);b=copy.deepcopy(a);b['arm']='structure_failure_atr';b['entry_identity']['price']=101
    pair=compare_pairs([a,b],'old_ma60_5','structure_failure_atr','A','entry_event_value')
    assert pair['valuation']['unknown']==1 and not pair['pairs'][0]['same_entry']


def test_B_compares_opportunities_not_claim_same_entry():
    t,p,f=fixture();a=value_event(t,p,f);a.update(group='B',arm='old_trigger');b=copy.deepcopy(a);b.update(arm='pullback',entry_identity=None)
    pair=compare_pairs([a,b],'old_trigger','pullback','B','capital_opportunity_value')
    assert pair['valuation']['known']==1 and 'NOT_same_entry' in pair['attribution']


def test_cluster_deterministic_and_unknown_preserved():
    pairs=[dict(code=str(i%2),fold=i//2,delta=.01*i) for i in range(6)]+[dict(code='U',fold=0,delta=None)]
    a=crossed_interval(pairs,'delta');b=crossed_interval(pairs,'delta')
    assert a==b and a['unknown']==1 and a['stock_clusters']==2 and a['time_clusters']==3


def test_all_episode_direction_can_reverse_double_closed():
    # Explicit mechanism fixture: one pair favors early exit among closures,
    # another still-held old event rises strongly. Never discard latter.
    t,p,f=fixture(True);a=value_event(t,p,f);b=copy.deepcopy(a);b.update(arm='structure_failure_atr',entry_event_value=a['entry_event_value']+.02)
    aa=copy.deepcopy(a);aa.update(episode_id='e2',entry_event_value=.3,valuation_status='unrealized_at_exact_cutoff')
    bb=copy.deepcopy(b);bb.update(episode_id='e2',entry_event_value=0.)
    v=compare_pairs([a,b,aa,bb],'old_ma60_5','structure_failure_atr','A','entry_event_value')['valuation']
    assert v['total']==2 and v['mean_known']==pytest.approx(-.14)


def test_real_71_episode_values_match_independent_review():
    import json
    from pathlib import Path
    from statistics import mean
    from mt1.history_cutoff import load_panel
    source=Path(__file__).resolve().parents[1]/'reports/mt12-history-20260912'
    original=json.loads((source/'full-results.json').read_text())
    folds={(f['code'],f['fold']):f for f in original['folds']};panels={};values={}
    for t in original['trades']:
        if t['group']!='A' or t['scenario']!='open_price_limit_base_v1' or t['cost_bps']!=15:continue
        if t['code'] not in panels:panels[t['code']]=load_panel(source,t['code'])
        r=value_event(t,panels[t['code']],folds[t['code'],t['fold']]);values.setdefault(t['arm'],[]).append(r)
        independently=t['net_price_return'] if t['status']=='closed' else t['unrealized_price_mark']
        assert r['entry_event_value']==pytest.approx(independently,abs=1e-12)
    expected={'old_ma60_5':.005216723327677596,'structure_failure':.0032905346633852004,'structure_failure_atr':.0035670441334045874}
    for arm,rs in values.items():
        assert len(rs)==71 and all(r['full_path_drawdown'] is not None for r in rs)
        assert mean(r['entry_event_value'] for r in rs)==pytest.approx(expected[arm],abs=1e-12)


def test_B_full_opportunity_denominator_and_unknowns():
    from pathlib import Path
    from mt1.history_cutoff import build
    source=Path(__file__).resolve().parents[1]/'reports/mt12-history-20260912'
    d=build(source)
    for arm,known in [('old_trigger',105),('breakout',104),('pullback',105)]:
        s=d['summary'][f'open_price_limit_base_v1|15|B|{arm}|capital_opportunities']
        assert s['total']==108 and s['known']==known and s['mean_over_full_denominator'] is None
        assert s['execution_or_price_or_observation_unknown']==108-known


def test_free_hk_probe_never_contains_denied_api_routes():
    from mt1.hk_free_probe import URLS
    assert all('tushare' not in u and 'hk_adjfactor' not in u and 'hk_daily_adj' not in u for u in URLS.values())


def test_hk_offline_missing_does_not_network(tmp_path,monkeypatch):
    from mt1.hk_free_probe import fetch
    import urllib.request
    monkeypatch.setattr(urllib.request,'urlopen',lambda *a,**kw:pytest.fail('unexpected_network'))
    with pytest.raises(ValueError,match='offline_checkpoint_missing'):fetch(tmp_path,'test','https://example.com',True)


def test_hk_cached_response_hash_checked(tmp_path):
    import json
    from mt1.hk_free_probe import fetch
    (tmp_path/'x.raw').write_text('altered')
    (tmp_path/'x.meta.json').write_text(json.dumps(dict(url='https://example.com',sha256='invalid')))
    with pytest.raises(ValueError,match='checkpoint_changed'):fetch(tmp_path,'x','https://example.com',True)
