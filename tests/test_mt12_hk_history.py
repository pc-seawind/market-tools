"""Synthetic execution constraints only; historical efficacy lives in r3 raw/results."""
import copy
import json
from pathlib import Path
import pytest
from mt1.hk_history import next_open,choose_price,news_coverage,write,signals,contract,replay_trade,calendar
from test_mt12_history_research import panel as cn_fixture

BASE='hk_next_open_v1'
STRESS='hk_vcm_day_conservative_v1'


def panel():
    p=cn_fixture();p['market']='HK'
    for b,e in zip(p['bars'],p['evidence']):
        e.pop('limit');e.update(open_eligibility='disclosure_reconstructed_no_halt',factor_verified=True,vcm_records=[],
            open_at=b['open_at'],close_at=b['close_at'],open_window='09:20-09:30',timestamp_basis='session_window_not_tick')
    return p


def price(v=100,vol=20):return dict(open=v,high=v+1,low=v-1,close=v,volume=vol)


def test_next_session_not_signal_close():
    f=next_open(panel(),180,240,'buy',BASE)
    assert f['index']==181 and f['model']==BASE and 'known_at' not in f


def test_no_CN_Tplus1_resale_lock():
    f=next_open(panel(),180,240,'sell',BASE,entry=181)
    assert f['index']==181 and 'NO_resale_lock' in f['settlement']


def test_cannot_sell_before_owning_but_same_day_allowed():
    assert next_open(panel(),180,240,'sell',BASE,entry=183)['index']==183


@pytest.mark.parametrize('side,value',[('buy',150),('sell',50)])
def test_gap_not_CN_price_limited(side,value):
    p=panel();b=p['bars'][181];b.update(open=value,high=value+1,low=value-1,close=value);b['raw']={k:b[k] for k in ('open','high','low','close')}
    f=next_open(p,180,240,side,BASE)
    assert f['index']==181 and f['raw_price']==value


@pytest.mark.parametrize('field',['eligibility','bar','factor','volume'])
def test_unknown_stops_instead_of_skipping(field):
    p=panel()
    if field=='eligibility':p['evidence'][181]['open_eligibility']='unknown'
    if field=='bar':p['bars'][181]=None
    if field=='factor':p['evidence'][181]['factor_verified']=False
    if field=='volume':p['bars'][181]['vol']=0
    assert next_open(p,180,240,'buy',BASE)['status']=='unknown'


def test_verified_suspension_skips():
    p=panel();p['evidence'][181]['open_eligibility']='verified_suspended';p['bars'][181]=None
    assert next_open(p,180,240,'buy',BASE)['index']==182


def test_VCM_not_daily_price_limit():
    p=panel();p['evidence'][181]['vcm_records']=['10:00-10:05']
    assert next_open(p,180,240,'buy',BASE)['index']==181
    f=next_open(p,180,240,'buy',STRESS)
    assert f['index']==182 and 'ex_post' in f['skipped'][0]['reason']


def test_fold_boundary_no_future_fill():
    assert next_open(panel(),239,240,'buy',BASE)['status']=='pending'


def test_delayed_open_uses_actual_window():
    p=panel();p['evidence'][181].update(open_at='20210914T14:00:00+08:00',open_window='14:00')
    f=next_open(p,180,240,'buy',BASE)
    assert '14:00' in f['open_at'] and f['open_window']=='14:00'


def test_price_two_vendor_rule_no_favorable_selection():
    assert choose_price(price(),price())[1]=='two_vendor_agreement'
    assert choose_price(price(99),price(100),price(100))[0]['open']==100
    assert choose_price(price(101),price(100),price(100))[0]['open']==100
    assert choose_price(price(101),price(100),price(101))[0]['open']==101


@pytest.mark.parametrize('third',[None,price(102),price(100,0)])
def test_unresolved_prices_unknown(third):
    assert choose_price(price(99),price(),third)[0] is None


def test_zero_flat_bar_is_not_suspend_evidence():
    assert choose_price(price(100,0),price(100,0),price(100,0))[0] is None


def test_no_signal_lookahead():
    p=panel();s,_=signals(p,contract());q=copy.deepcopy(p)
    for b in q['bars'][201:]:
        for k in ('open','high','low','close'):b[k]*=3
    t,_=signals(q,contract())
    assert {i:v for i,v in s.items() if i<=200}=={i:v for i,v in t.items() if i<=200}


def test_HK_cost_key_not_CN():
    p=panel();a=contract();b=copy.deepcopy(a);b['execution']['per_side_cost_bps']['CN']=9999
    assert replay_trade(p,180,240,'structure_failure_atr',a,BASE)==replay_trade(p,180,240,'structure_failure_atr',b,BASE)


def test_cost_increase_lowers_common_mark_not_signal():
    p=panel();a=contract();b=copy.deepcopy(a);b['execution']['per_side_cost_bps']['HK']=75
    x=replay_trade(p,180,240,'structure_failure_atr',a,BASE);y=replay_trade(p,180,240,'structure_failure_atr',b,BASE)
    assert x['entry_fill']==y['entry_fill'] and x['unrealized_price_mark']>y['unrealized_price_mark']


@pytest.mark.parametrize('bad',['empty','truncated','wrong_stock','wrong_date','duplicate'])
def test_empty_or_invalid_news_not_eligibility(tmp_path,bad):
    row=dict(NEWS_ID='1',STOCK_CODE='00005',DATE_TIME='02/01/2020 10:00',LONG_TEXT='Ordinary notice')
    rows=[row]
    if bad=='empty':rows=[]
    if bad=='wrong_stock':row['STOCK_CODE']='00941'
    if bad=='wrong_date':row['DATE_TIME']='02/01/2021 10:00'
    if bad=='duplicate':rows=rows*2
    write(tmp_path/'corpus_00005_2020.raw',dict(result=json.dumps(rows),hasNextRow=bad=='truncated',recordCnt=len(rows),loadedRecord=len(rows)))
    with pytest.raises(ValueError):news_coverage(tmp_path,'00005')


def test_real_calendar_and_disputed_dates_readonly(tmp_path):
    # Official evidence regression, not a synthetic profit test.
    root=Path(__file__).resolve().parents[1];out=root/'reports/mt12-history-20260912-r3'
    c=json.loads((out/'market-calendar.json').read_text());lookup={s['date']:s for s in c['sessions']}
    assert len(lookup)==1228 and len(c['half_days'])==10
    for d in ('20230717','20230901','20230908','20240906','20211013'):assert d not in lookup
    for d,t in [('20210628','13:30'),('20220825','13:00'),('20231009','14:00')]:assert t in lookup[d]['open_at']
    assert '13:55' in lookup['20221102']['close_at']
    assert lookup['20241224']['half_day']
    disputes=json.loads((out/'third-price-audit.json').read_text());assert len(disputes)==34
    assert all(d['matches']==['Tencent'] for d in disputes)


def test_positive_halt_not_silently_admitted(tmp_path):
    for year in range(2020,2026):
        row=dict(NEWS_ID=str(year),STOCK_CODE='00005',DATE_TIME=f'02/01/{year} 10:00',LONG_TEXT='Trading Halt' if year==2023 else 'Ordinary notice')
        write(tmp_path/f'corpus_00005_{year}.raw',dict(result=json.dumps([row]),hasNextRow=False,recordCnt=1,loadedRecord=1))
    for cat in ('17650','17850','17960'):
        write(tmp_path/f'halts_00005_{cat}.raw',dict(result='[]',hasNextRow=False,recordCnt=0,loadedRecord=0))
    assert news_coverage(tmp_path,'00005')['status']=='unknown_unparsed_halt_intervals'


@pytest.mark.parametrize('scenario',['open_price_limit_base_v1','arbitrary'])
def test_reject_CN_or_unknown_model(scenario):
    with pytest.raises(ValueError):next_open(panel(),180,240,'buy',scenario)
