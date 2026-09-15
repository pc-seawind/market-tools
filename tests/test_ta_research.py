"""SYNTHETIC_ONLY unit fixtures; excluded from nine-stock live validation."""
from copy import deepcopy
from pathlib import Path
import json
import pytest
from mt1 import ta_research as ta
from mt1.ta_consumer import consume
from mt1.ta_review import evaluate

ASOF='2026-09-15T19:00:00+08:00'

@pytest.fixture
def quote(tmp_path):
    p=tmp_path/'raw';p.write_bytes(b'SYNTHETIC_ONLY')
    return ta.evidence('001309.SZ','quote',{'code':'001309.SZ','last':10.0,'low':9.0,'high':11.0,'provider_at':'2026-09-15T16:00:00+08:00'},'https://test.invalid','2026-09-15T16:00:00+08:00','2026-09-15T17:00:00+08:00',p,currency='CNY',unit='currency_per_share',adjustment='unadjusted_spot',calendar_gate={'allowed':True,'expected_date':'2026-09-15'})

def output(e):
    c={'text':'报价已取得，经营逻辑尚待核实','evidence_ids':[e['evidence_id']],'numbers':[]}
    return {'facts':[c],'bull_case':[c],'bear_case':[c],'disagreements':[c],'adjudication':c,'gaps':['缺公司披露'],'held_direction':'暂待复核','unheld_direction':'等待','short_term':{'target_date':None,'scenario':'震荡观察','trigger':'取得量价承接证据','invalidation':'持续破坏'},'thesis':{'horizon':'一至三个月','proposition':'盈利兑现待验证','next_evidence':'公司季报','invalidation':'经营证伪'},'next_review_date':'2026-09-16'}

def test_quote_positive(quote): assert ta.evidence_errors(quote,ASOF)==[]
@pytest.mark.parametrize('field,value,err', [('published_at','2026-09-16T00:00:00+08:00','future_publication'),('fetched_at','2026-09-16T00:00:00+08:00','future_fetch'),('published_at',None,'publication_unknown'),('currency','HKD','currency_conflict')])
def test_evidence_negative(quote,field,value,err):
    quote[field]=value;assert err in ta.evidence_errors(quote,ASOF)

def test_stale_quote(quote):
    quote['content']['provider_at']='2026-09-14T16:00:00+08:00';assert 'stale_quote' in ta.evidence_errors(quote,ASOF)
def test_quote_invalid_number(quote):
    quote['content']['last']=12;assert 'numeric_conflict' in ta.evidence_errors(quote,ASOF)
def test_foreign_code(quote):
    quote['content']['code']='00700.HK';assert 'foreign_quote' in ta.evidence_errors(quote,ASOF)
def test_hash_changed(quote):
    Path(quote['raw_path']).write_text('tampered');assert 'raw_hash_mismatch' in ta.evidence_errors(quote,ASOF)
def test_output_valid(quote):assert ta.validate_output(output(quote),[quote])==[]
def test_false_reference(quote):
    o=output(quote);o['facts'][0]['evidence_ids']=['fake'];assert 'invalid_reference' in ta.validate_output(o,[quote])
def test_hypothesis_cannot_be_fact(quote):
    quote['kind']='hypothesis';assert 'hypothesis_as_fact' in ta.validate_output(output(quote),[quote])
def test_precision_rejected(quote):
    o=output(quote);o['held_direction']='上涨概率80%';assert 'unsupported_precision_in_prose' in ta.validate_output(o,[quote])
def test_numeric_contract(quote):
    o=output(quote);o['facts'][0]['numbers']=[{'evidence_id':quote['evidence_id'],'field':'last','value':10.0,'unit':'currency_per_share','currency':'CNY'}]
    assert ta.validate_output(o,[quote])==[]
    o['facts'][0]['numbers'][0]['value']=99;assert 'numeric_conflict' in ta.validate_output(o,[quote])
@pytest.mark.parametrize('c,s',[('001309.SZ','sz001309'),('603986.SH','sh603986'),('00700.HK','hk00700')])
def test_code_mapping(c,s):assert ta.symbol(c)==s
def test_no_bad_code():
    with pytest.raises(ValueError):ta.symbol('700.HK')
def test_immutable_save(tmp_path):
    p=tmp_path/'x';ta.save(p,{'x':1});before=p.read_bytes();ta.save(p,{'x':1});assert p.read_bytes()==before
    with pytest.raises(ValueError):ta.save(p,{'x':2})
def test_no_result_does_not_block(tmp_path):
    r=consume(ASOF,tmp_path);assert r['status']=='research_unavailable' and r['technical_actions_unchanged']
def test_not_matured():
    r=evaluate(ASOF,ASOF,[],{},[],[]);assert all(x['status']=='not_matured' for x in r['horizons']);assert r['return_decomposition']['market'] is None

def test_price_cannot_prove_business(quote):
    with pytest.raises(ValueError):evaluate('2026-09-14T19:00:00+08:00',ASOF,[],{},[quote],[{'status':'confirmed','evidence_ids':[quote['evidence_id']],'reviewer':'test','rationale':'price_up'}])
def test_outcome_requires_new_evidence():
    with pytest.raises(ValueError):evaluate(ASOF,ASOF,[],{},[],[{'status':'confirmed','evidence_ids':[],'reviewer':'test','rationale':'price_up'}])
def test_model_checkpoint_no_network(tmp_path,quote,monkeypatch):
    import requests
    calls=[]
    class R:
        status_code=200
        def json(self):return {'id':'synthetic','model':ta.MODEL['model'],'stop_reason':'end_turn','usage':{'input_tokens':1,'output_tokens':1},'content':[{'type':'text','text':json.dumps(output(quote))}]}
    monkeypatch.setenv('ANTHROPIC_HUOSHAN_URL','https://ark.cn-beijing.volces.com/api/coding');monkeypatch.setenv('HUOSHAN_API_KEY','SYNTHETIC')
    monkeypatch.setattr(requests,'post',lambda *a,**kw:(calls.append(1) or R()))
    payload={'as_of':ASOF,'short_target_session':None}
    a=ta.call_model(tmp_path,'B',payload,[quote]);b=ta.call_model(tmp_path,'B',payload,[quote])
    assert a==b and len(calls)==1 and not a['errors']
    p=tmp_path/'B.response.json';p.write_text('{}')
    with pytest.raises(ValueError):ta.call_model(tmp_path,'B',payload,[quote])
def test_retry_bound_and_partial_failure(tmp_path,quote,monkeypatch):
    import requests
    calls=[]
    monkeypatch.setenv('ANTHROPIC_HUOSHAN_URL','https://ark.cn-beijing.volces.com/api/coding');monkeypatch.setenv('HUOSHAN_API_KEY','SYNTHETIC')
    def fail(*a,**kw):calls.append(1);raise requests.Timeout()
    monkeypatch.setattr(requests,'post',fail)
    r=ta.call_model(tmp_path,'B',{},[quote]);assert r['errors']==['model_call_failed'] and len(calls)==2
    assert ta.call_model(tmp_path,'B',{},[quote])==r and len(calls)==2

def test_strict_no_unchecked_numeric_extension(quote):
    from mt1.ta_audit import strict_output
    o=output(quote);o['target_price']=9999
    assert 'unexpected_output_fields' in strict_output(o,[quote])
def test_missing_adjudication_reference(quote):
    from mt1.ta_audit import strict_output
    o=output(quote);o['adjudication']['evidence_ids']=[]
    assert 'unreferenced_claim' in strict_output(o,[quote])
def test_product_identifier_not_financial_probability(quote):
    from mt1.ta_audit import strict_output
    quote['content']['identifier']='1.6T'
    o=output(quote);o['gaps']=['1.6T订单未核']
    assert not strict_output(o,[quote])
    o['gaps']=['1.6T订单概率80%'];assert 'unsupported_precision_in_prose' in strict_output(o,[quote])

def test_numeric_string_equivalence_not_conflict():
    assert ta.same_number('178.91',178.91)
    assert not ta.same_number('178.91',178.92)
    assert not ta.same_number('NaN','NaN')
    assert not ta.same_number(True,1)

def test_business_new_evidence_positive(tmp_path):
    p=tmp_path/'source';p.write_text('SYNTHETIC_ONLY:new disclosure')
    e=ta.evidence('001309.SZ','company_primary',{'text':'SYNTHETIC_ONLY'},'https://test.invalid','2026-09-16T09:00:00+08:00','2026-09-16T10:00:00+08:00',p)
    r=evaluate(ASOF,'2026-09-16T19:00:00+08:00',[],{},[e],[{'status':'refuted','evidence_ids':[e['evidence_id']],'reviewer':'SYNTHETIC_TEST','rationale':'explicit company disclosure'}])
    assert r['business_propositions'][0]['status']=='refuted' and r['return_decomposition']['market'] is None

def test_price_observation_not_causal_and_correct_horizon(tmp_path):
    from datetime import timedelta
    ds=[(ta.instant(ASOF)+timedelta(days=i)).isoformat() for i in range(1,22)] # fixture-supplied sessions, not real calendar
    data={'code':'001309.SZ','currency':'CNY','basis':'unadjusted_close_price_only','rows':[{'code':'001309.SZ','currency':'CNY','close_at':d,'close':100+i} for i,d in enumerate(ds)]}
    p=tmp_path/'panel';p.write_text(json.dumps(data))
    e=ta.evidence('001309.SZ','price_panel',data,'https://test.invalid',ds[-1],ds[-1],p)
    cal={'days':[{'close_at':d,'is_open':True} for d in ds]}
    cp=tmp_path/'calendar';cp.write_text(json.dumps(cal))
    ce=ta.evidence('001309.SZ','calendar',cal,'https://test.invalid',ds[-1],ds[-1],cp)
    r=evaluate(ASOF,ds[-1],ds,{'20':{'price_panel_id':e['evidence_id']}},[e],[],ce)
    assert r['horizons'][0]['return']==pytest.approx(.2)
    assert r['horizons'][1]['status']=='not_matured'
    assert r['business_status']=='blocked'
    assert r['return_decomposition']['residual'] is None

def synthetic_run(tmp_path, quote):
    from mt1.ta_review import register
    root=tmp_path/'run';root.mkdir()
    e=deepcopy(quote);e['raw_path']=str(Path(e['raw_path']))
    out=output(e);frozen={'as_of':ASOF,'scope_epoch':'SYNTHETIC_ONLY','scope_hash':'test','stocks':[{'code':'001309.SZ','evidence':[e]}]}
    result={'run_id':'SYNTHETIC_ONLY','as_of':ASOF,'scope_epoch':'SYNTHETIC_ONLY','stocks':[{'code':'001309.SZ','status':'pass','calls':{'C':{'output':out}},'blockers':[]}]}
    ta.save(root/'input.json',frozen);ta.save(root/'results.json',result);ta.save(root/'001309.SZ/result.json',result['stocks'][0])
    files=[{'path':str(p.relative_to(root)),'sha256':ta.file_hash(p)} for p in root.rglob('*') if p.is_file()]
    ta.save(root/'manifest.json',{'run_id':'SYNTHETIC_ONLY','input_hash':ta.digest(frozen),'files':files})
    return root/'manifest.json'

def test_existing_longitudinal_registration_idempotent(tmp_path,quote):
    from mt1.ta_review import register
    from mt1.longitudinal import manifests,weekly_index
    mp=synthetic_run(tmp_path,quote);root=tmp_path/'archive'
    a=register(mp,root);b=register(mp,root)
    assert len(manifests(root))==1
    index=weekly_index(root,asof='2026-09-15',current_epoch='SYNTHETIC_ONLY')
    # Record time is real test execution time; material registration is independently visible.
    assert any(e['kind']=='company_research' for _,_,m in manifests(root) for e in m['events'])

def test_consumer_scope_hash_and_archive_corruption(tmp_path,quote):
    mp=synthetic_run(tmp_path,quote);root=tmp_path/'pointer';root.mkdir()
    ta.save(root/'latest.json',{'manifest':str(mp),'sha256':ta.file_hash(mp)})
    (mp.parent/'results.json').write_text('{}')
    r=consume(ASOF,root,tmp_path/'absent-scope')
    assert r['status']=='research_unavailable' and r['technical_actions_unchanged']

def test_run_stock_isolation_and_resume(tmp_path,quote,monkeypatch):
    from mt1 import ta_review
    second=deepcopy(quote);second['code']='603986.SH';second['content']['code']='603986.SH'
    frozen={'as_of':ASOF,'scope_epoch':'SYNTHETIC_ONLY','stocks':[{'code':code,'name':'SYNTHETIC_ONLY','evidence':[e],'collection_errors':[],'A':{},'target_session':'2026-09-16'} for code,e in [('001309.SZ',quote),('603986.SH',second)]]}
    monkeypatch.setattr(ta,'freeze',lambda *a:frozen)
    monkeypatch.setattr(ta_review,'register',lambda *a:{'synthetic':True})
    calls=[]
    def call(d,role,payload,es):
        calls.append((payload['stock']['code'],role))
        if payload['stock']['code']=='001309.SZ':raise RuntimeError('SYNTHETIC_FAILURE')
        return {'errors':[],'output':output(es[0])}
    monkeypatch.setattr(ta,'call_model',call)
    root=tmp_path/'research';first=ta.run(root,tmp_path,tmp_path/'scope')
    results=ta.read(Path(first['manifest']).parent/'results.json')['stocks']
    assert results[0]['status']=='blocked'
    assert len(results[1]['calls'])==6
    assert ta.run(root,tmp_path,tmp_path/'scope')==first and len(calls)==7

def test_day_change_not_open_change():
    f=['0']*50
    for k,v in {2:'001309',3:'10',4:'11',5:'9',6:'100',30:'20260915160000',33:'12',34:'8',47:'13',48:'5'}.items():f[k]=v
    raw=('v_sz001309="'+'~'.join(f)+'";').encode('gbk')
    q=ta.research_quote(raw,'001309.SZ')
    assert q['previous_close']==11 and q['close_vs_open']=='higher' and q['close_vs_previous']=='lower'

def test_maturity_cannot_use_unverified_weekdays():
    with pytest.raises(ValueError,match='calendar_evidence_required'):
        evaluate(ASOF,'2026-09-16T19:00:00+08:00',['2026-09-16T15:00:00+08:00'],{},[],[])

def test_interrupted_collection_retains_previous_raw(tmp_path):
    first=ta.collection_directory(tmp_path);p=first/'response';p.write_text('failed original')
    retry=ta.collection_directory(tmp_path)
    assert first!=retry and p.read_text()=='failed original'
