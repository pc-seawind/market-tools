"""Historical-model tests. Synthetic fixtures are not performance evidence."""
import copy
import json
from pathlib import Path
import pytest
from mt1.history_research import Collector, next_open, history_prefix, signals, replay_trade, sha, write
from mt1.timing import contract

BASE='open_price_limit_base_v1'
STRICT='any_limit_touch_conservative_v1'


def panel(n=250):
    from datetime import date,timedelta
    bars=[];evidence=[];d=date(2021,1,4)
    for i in range(n):
        while d.weekday()>4:d+=timedelta(days=1)
        day=d.strftime('%Y%m%d');c=100+i*.1
        raw=dict(open=c,high=c+.3,low=c-.3,close=c+.1)
        e=dict(date=day,suspension=[],limit=dict(up_limit=c*1.1,down_limit=c*.9),realization_at=str(d)+'T15:00:00+08:00')
        b=dict(date=day,**raw,raw=raw.copy(),factor=1,vol=100,index=i,evidence=e,
               open_at=str(d)+'T09:30:00+08:00',close_at=e['realization_at'])
        bars.append(b);evidence.append(e);d+=timedelta(days=1)
    return dict(code='SYNTHETIC',bars=bars,evidence=evidence,dates=[b['date'] for b in bars],sources=[],benchmark={b['date']:100 for b in bars})


def test_next_open_not_same_bar():
    p=panel();f=next_open(p,180,240,'buy',BASE)
    assert f['index']==181 and f['raw_price']==p['bars'][181]['raw']['open']
    assert 'known_at' not in f


def test_gap_fills_open_not_signal_or_stop():
    p=panel();p['bars'][181]['open']=125;p['bars'][181]['raw']['open']=125
    f=next_open(p,180,240,'buy',BASE)
    assert f['price']==125 and f['gap_from_signal_close']>0


@pytest.mark.parametrize('side,limit',[('buy','up_limit'),('sell','down_limit')])
def test_side_limit_delay(side,limit):
    p=panel();p['bars'][181]['raw']['open']=p['evidence'][181]['limit'][limit]
    f=next_open(p,180,240,side,BASE)
    assert f['index']==182 and len(f['skipped'])==1


def test_opposite_limit_not_blocked():
    p=panel();p['bars'][181]['raw']['open']=p['evidence'][181]['limit']['down_limit']
    assert next_open(p,180,240,'buy',BASE)['index']==181


def test_suspension_skips_verified_session():
    p=panel();p['bars'][181]=None;p['evidence'][181]['suspension']=[dict(suspend_type='S')]
    assert next_open(p,180,240,'buy',BASE)['index']==182


@pytest.mark.parametrize('missing',['bar','limit','limit_value'])
def test_unknown_does_not_skip_to_favorable_later_open(missing):
    p=panel()
    if missing=='bar':p['bars'][181]=None
    elif missing=='limit':p['evidence'][181]['limit']=None
    else:p['evidence'][181]['limit']['up_limit']=None
    assert next_open(p,180,240,'buy',BASE)['status']=='unknown'


def test_t_plus_one():
    p=panel();assert next_open(p,180,240,'sell',BASE,entry=181)['index']==182


def test_fold_end_no_leak():
    p=panel();assert next_open(p,239,240,'buy',BASE)['status']=='pending'


def test_conservative_is_expost_not_signal():
    p=panel();p['bars'][181]['raw']['high']=p['evidence'][181]['limit']['up_limit']
    assert next_open(p,180,240,'buy',BASE)['index']==181
    assert next_open(p,180,240,'buy',STRICT)['index']==182


def test_history_gap_restarts_warmup():
    p=panel();p['bars'][180]=None
    assert len(history_prefix(p['bars'],200))==20
    ss,cov=signals(p,contract());assert 200 not in ss and cov['missing_bar']==1


def test_future_prices_do_not_change_past_signal():
    p=panel();s,_=signals(p,contract());q=copy.deepcopy(p)
    for b in q['bars'][201:]:
        for k in ('open','high','low','close'):b[k]*=3
    t,_=signals(q,contract())
    assert {i:v for i,v in s.items() if i<=200}=={i:v for i,v in t.items() if i<=200}


def test_future_execution_evidence_never_changes_signal():
    p=panel();s,_=signals(p,contract());q=copy.deepcopy(p)
    for e in q['evidence']:e['limit']=None;e['suspension']=[dict(suspend_type='S')]
    t,_=signals(q,contract());assert s==t


def test_fee_sensitivity_closed_trade():
    p=panel()
    for i in range(190,250):
        p['bars'][i].update(open=95,high=96,low=94,close=95,raw=dict(open=95,high=96,low=94,close=95))
        p['evidence'][i]['limit']=dict(up_limit=105,down_limit=85)
    cfg=contract();a=replay_trade(p,180,240,'structure_failure_atr',cfg,BASE)
    cfg['execution']['per_side_cost_bps']['CN']=50
    b=replay_trade(p,180,240,'structure_failure_atr',cfg,BASE)
    assert a['status']==b['status']=='closed'
    assert b['net_price_return']<a['net_price_return'] and a['entry_fill']==b['entry_fill'] and a['exit_fill']==b['exit_fill']


def test_terminal_censoring_not_force_close():
    p=panel();t=replay_trade(p,180,185,'old_ma60_5',contract(),BASE)
    assert t['status']=='not_matured' and t['net_price_return'] is None and t['turnover']==1


def test_adjusted_entry_price_raw_limit_comparison():
    p=panel();p['bars'][181]['factor']=2;p['bars'][181]['open']*=2
    t=next_open(p,180,240,'buy',BASE)
    assert t['status']=='filled' and t['price']==2*t['raw_price']


def test_offline_missing_checkpoint(tmp_path):
    with pytest.raises(ValueError,match='offline_checkpoint_missing'):
        Collector(tmp_path,True).get('daily',ts_code='TEST')


def test_checkpoint_hash_tamper(tmp_path):
    from mt1.timing import digest
    c=Collector(tmp_path,True);request=dict(api_name='daily',params=dict(ts_code='TEST'),fields='')
    key='daily-'+digest(request)[:20];p=tmp_path/'raw'/(key+'.json');p.write_text('{}')
    write(tmp_path/'raw'/(key+'.meta.json'),dict(request=request,sha256='wrong'))
    with pytest.raises(ValueError,match='raw_checkpoint_changed'):c.get('daily',ts_code='TEST')


def test_frozen_scope_not_user_holdings():
    spec=json.loads((Path(__file__).resolve().parents[1]/'reports/mt12-history-20260912/frozen-contract.json').read_text())
    assert not set(spec['codes'])&set(spec['excluded']['user_holdings'])
    assert len(spec['codes'])==len(set(spec['codes']))==12
    assert spec['start']=='20210101' and spec['end']=='20251231' and spec['trial_contract']==contract()


def test_no_modification_to_forward_executor():
    from mt1.timing_experiment import next_fill
    p=panel()
    assert next_fill(p['bars'],180,'buy','CN',contract())['status']=='unknown'

@pytest.mark.parametrize('mutation',['holding','duplicate','span','parameter'])
def test_invalid_research_spec_rejected(mutation):
    from mt1.history_research import validate_spec
    path=Path(__file__).resolve().parents[1]/'reports/mt12-history-20260912/frozen-contract.json'
    s=json.loads(path.read_text())
    if mutation=='holding':s['codes'].append(s['excluded']['user_holdings'][0])
    elif mutation=='duplicate':s['codes'].append(s['codes'][0])
    elif mutation=='span':s['start']='20240101'
    else:s['trial_contract']['atr_multiple']=2
    with pytest.raises(ValueError):validate_spec(s)
