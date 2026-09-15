"""Actual archived TrendForce pages; no network/model calls."""
import copy
from pathlib import Path
import pytest
from mt1.ta_evidence import admit, coverage
from mt1.ta_research import read, validate_output
from mt1.timing_cli import file_hash

ROOT=Path(__file__).resolve().parents[1]/'reports/ta10-closeout-20260916'


def source(key):
    data=read(ROOT/'collection.json')
    url=read(ROOT/'bounded-receipt.json')['targeted'][key]['url']
    old=next(e for s in data['stocks'] for e in s['evidence'] if e['url']==url)
    receipt={**old,'status':'ok'}; spec=copy.deepcopy(old['source_review']['spec'])
    body=Path(receipt['text_path']).read_text()
    date='3 July 2026' if key=='memory' else '16 June 2026'
    date_pos=body.index('\n'+date+'\n')
    start=body.rfind('\n',0,date_pos)+1
    end=body.index('若有兴趣' if key=='memory' else '若有興趣',date_pos)
    spec.update(kind='institution_forecast',source_window={'char_start':start,'char_end':end,
                'text_sha256':file_hash(receipt['text_path']),'reviewer':'source-review-boundary'})
    return old,receipt,spec,body


@pytest.mark.parametrize('key',['memory','niche'])
def test_real_forecast_not_facts_but_background_allowed(key):
    old,receipt,spec,body=source(key)
    e,err=admit(old['code'],spec,receipt,'2026-09-17T00:00:00+08:00')
    assert err is None and e['kind']=='institution_forecast' and e['background']
    claim={'text':'该机构预计价格上行','evidence_ids':[e['evidence_id']],'numbers':[]}
    output={'facts':[claim],'bull_case':[],'bear_case':[],'disagreements':[],
            'adjudication':{},'gaps':[],'held_direction':'未知','unheld_direction':'未知',
            'short_term':{'target_date':None,'scenario':'未知','trigger':'未知','invalidation':'未知'},
            'thesis':{'horizon':'未知','proposition':'未知','next_evidence':'未知','invalidation':'未知'},
            'next_review_date':'2026-09-17'}
    assert 'nonfact_source_as_fact' in validate_output(output,[e])
    output['facts']=[];output['bull_case']=[claim]
    assert validate_output(output,[e])==[]
    c=coverage({'code':old['code'],'name':'test'},[{'topic':'industry','task_id':'test','status':'hits'}],[e],{'profiles':{}},'2026-09-17T00:00:00+08:00')
    assert next(r for r in c['rows'] if r['topic']=='industry')['status']=='blocked'


@pytest.mark.parametrize('key',['memory','niche'])
def test_real_navigation_and_later_recommendations_excluded(key):
    old,receipt,spec,body=source(key)
    before=Path(receipt['text_path']).read_bytes()
    e,err=admit(old['code'],spec,receipt,'2026-09-17T00:00:00+08:00')
    assert err is None
    window=e['content']['source_window']
    for span in e['content']['sections']:
        a,b=span['char_start'],span['char_end']
        assert window['char_start']<=a<b<=window['char_end']
        assert span['text']==body[a:b]
        assert span['paragraph']==body[:a].count('\n')+1
        assert not any(t in span['text'] for t in ['2026.09.14','相关文章','相關文章','上一则','上一則','会员登录','會員登入'])
    # A metadata verification term present ONLY in adjacent recommendations
    # cannot certify this article under its older publication date.
    spec['verify']=['2026.09.14']
    assert admit(old['code'],spec,receipt,'2026-09-17T00:00:00+08:00')[1]=='identity_or_publication_not_verified'
    assert Path(receipt['text_path']).read_bytes()==before


@pytest.mark.parametrize('change',[{'char_start':-1},{'char_end':999999},{'text_sha256':'wrong'},{'reviewer':''}])
def test_bad_boundary_does_not_fallback_to_whole_page(change):
    old,r,s,_=source('memory');s['source_window'].update(change)
    assert admit(old['code'],s,r,'2026-09-17T00:00:00+08:00')[1]=='invalid_source_window'


def test_numeric_selector_cannot_escape_window_and_offsets_are_absolute():
    old,r,s,body=source('memory')
    s['numbers']=[{'field':'forecast','pattern':r'预估3Q26一般型DRAM合约价将季增(?P<value>13)-18%',
                  'subject':'DRAM','metric':'growth','unit':'percent','currency':'none',
                  'period_start':'2026-07-01','period_end':'2026-09-30','scope':'contract',
                  'basis':'qoq','is_forecast':True},
                 {'field':'recommended','pattern':r'2026.09.(?P<value>14)',
                  'subject':'test','metric':'day','unit':'day','currency':'none',
                  'period_start':'2026-09-01','period_end':'2026-09-30','scope':'test',
                  'basis':'test','is_forecast':True}]
    e,err=admit(old['code'],s,r,'2026-09-17T00:00:00+08:00')
    assert err is None
    metrics=e['content']['numeric_metrics'];assert set(metrics)=={'forecast'}
    n=metrics['forecast'];assert body[n['char_start']:n['char_end']]=='13'
    assert n['is_forecast'] and n['evidence_type']=='institution_forecast'
