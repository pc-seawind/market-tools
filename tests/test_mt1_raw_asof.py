import pytest
from mt1.raw_asof import select_diagnostic, action_evidence, build_draft, raw_sources
from mt1.cache_union import Union


def test_future_and_unknown_not_eligible():
    a,u,f=select_diagnostic([{'ts_code':'x','ann_date':'20250101'},{'ts_code':'x'}],'x','2024-01-01','ann_date')
    assert not a and len(u)==len(f)==1

def test_no_self_reported_pit_admission(tmp_path):
    source={'api':'fina_indicator','source':{},'params':{},'fetched_at':'2026-09-11T00:00:00+08:00','rows':[{'ts_code':'000001.SZ','ann_date':'20230101','pit_verified':True,'end_date':'20221231','roe':10}]}
    result=build_draft('000001.SZ','x','2024-01-02T16:00:00+08:00',[],Union(tmp_path/'x.db'),[source])
    assert result['bundle']['inputs']=={}
    assert result['recompute']['status']=='blocked'
    assert not result['draft_inputs']['financial']['payload']
    assert result['draft_inputs']['financial']['mapping_candidates'][0]['roe']==10
    assert not result['replay_ready']

def test_no_delisting_zero_or_last_price():
    r=action_evidence([],'x','20240101','20240930','20240601')
    assert r['settlement'] is None and not r['replay_ready']
    assert any('delisting' in b for b in r['blockers'])

def test_incomplete_cash_date_rejected():
    r=action_evidence([{'ts_code':'x','div_proc':'实施','ex_date':'20240501'}],'x','20240101','20240930')
    assert not r['events'] and len(r['rejected_events'])==1

def test_future_dividend_not_used():
    r=action_evidence([{'ts_code':'x','ex_date':'20250101'}],'x','20240101','20240930')
    assert not r['events'] and not r['rejected_events']

def test_raw_hash_mismatch(tmp_path):
    import json
    p=tmp_path/'r.json';p.write_text('[]');s=tmp_path/'s.json';s.write_text(json.dumps({'results':[{'artifact':{'path':str(p),'sha256':'bad'}}]}))
    with pytest.raises(ValueError,match='hash'):raw_sources(s)

def test_future_revision_never_populates_financial_payload(tmp_path):
    rows=[{'ts_code':'000001.SZ','ann_date':'20230101','roe':1}, {'ts_code':'000001.SZ','ann_date':'20250101','roe':99}]
    s={'api':'fina_indicator','source':{},'params':{},'fetched_at':'2026-09-11T00:00:00+08:00','rows':rows}
    r=build_draft('000001.SZ','x','2024-01-02T16:00:00+08:00',[],Union(tmp_path/'x.db'),[s])
    f=r['draft_inputs']['financial'];assert f['payload']=={} and f['lineage']['excluded_future_rows']==1
    assert len(f['mapping_candidates'])==1 and f['mapping_candidates'][0]['roe']==1

def test_stock_dividend_without_credit_date_blocks():
    r=action_evidence([{'ts_code':'x','div_proc':'实施','ex_date':'20240502','record_date':'20240501','pay_date':'20240502','cash_div_tax':0,'stk_div':1}],'x','20240101','20240930')
    assert r['rejected_events'][0]['reasons']==['stock_credit_date_unknown']
