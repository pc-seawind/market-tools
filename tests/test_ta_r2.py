"""SYNTHETIC_ONLY negative/positive contracts, never nine-stock observations."""
import json
from pathlib import Path
from copy import deepcopy
import pytest
from mt1.ta_quality import semantic_errors
from mt1 import ta_pipeline as pipeline
from mt1.ta_sources import excerpts
from mt1.ta_research import SYSTEM, read, save, digest


def sample():
    return {'facts':[{'text':'当日下跌','evidence_ids':['q'],'numbers':[]}], 'disagreements':[],
        'short_term':{'scenario':'未知，无法判断方向','trigger':'未知，缺短期依据','invalidation':'未知，缺短期依据'}}
def stock():return {'evidence':[{'kind':'quote','content':{'close_vs_previous':'lower'}}]}
@pytest.mark.parametrize('text',['下季营收改善','下一季度利润扭亏','后续季度现金流恢复','季报披露存货降低','未来数月完成客户验证'])
def test_quarter_trigger_cannot_be_next_session(text):
    o=sample();o['short_term']['trigger']=text;assert 'cross_horizon_short_term' in semantic_errors(o,stock())
def test_unknown_short_term_is_honest():assert semantic_errors(sample(),stock())==[]
def test_observable_price_condition_not_quarterly():
    o=sample();o['short_term'].update(scenario='未知，观察冻结收盘价上下的条件走势',trigger='盘中报价高于冻结收盘价',invalidation='收盘报价低于冻结收盘价');assert semantic_errors(o,stock())==[]
def test_day_return_not_open():
    o=sample();o['facts'][0]['text']='当日上涨';assert 'wrong_day_change' in semantic_errors(o,stock())
def test_prices_do_not_refute_valuation():
    o=sample();o['disagreements']=[{'text':'低估值与今日下跌方向相反'}];assert 'price_cannot_refute_valuation' in semantic_errors(o,stock())
    o['disagreements'][0]['text']='今日下跌不能证伪低估值命题';assert not semantic_errors(o,stock())
def test_archive_excerpt_not_same():
    o=sample();o['gaps']=['存档仅两页'];assert 'archive_excerpt_confusion' in semantic_errors(o,stock())
def test_prompt_numbers_not_conflicting():
    assert '所有numbers数组必须是空数组' not in SYSTEM
    assert 'numbers不是必须为空' in SYSTEM

def test_exact_offsets_and_later_page():
    body='SYNTHETIC cover\f'+'x'*200+'\f经营活动产生的现金流量净额\n真实段落'+('z'*2000)+'\fInventories 20'
    for x in excerpts(body):assert x['text']==body[x['char_start']:x['char_end']]
    assert any(x['topic']=='cash_flow' and x['page']>1 for x in excerpts(body))
def test_nonproduction_caller_no_process(tmp_path,monkeypatch):
    monkeypatch.setattr(pipeline.subprocess,'run',lambda *a,**kw:pytest.fail('unexpected launch'))
    assert pipeline.request_refresh(tmp_path,'evening')['status']=='skipped_non_production_source'
    assert pipeline.request_refresh(tmp_path,'morning')['status']=='skipped_non_evening'

def fixture_report(tmp_path,monkeypatch):
    daily=tmp_path/'daily';report=daily/'day';report.mkdir(parents=True);(report/'sources.json').write_text('{}')
    scope=tmp_path/'scope';scope.write_text('{}');catalog=tmp_path/'catalog';catalog.mkdir()
    monkeypatch.setattr(pipeline,'DAILY',daily);return report,scope,catalog

def test_request_idempotence_and_new_source_revision(tmp_path,monkeypatch):
    report,scope,catalog=fixture_report(tmp_path,monkeypatch);calls=[]
    class R:returncode=0
    monkeypatch.setattr(pipeline.subprocess,'run',lambda *a,**kw:(calls.append(a) or R()))
    root=tmp_path/'pipeline';a=pipeline.request_refresh(report,'evening',root,scope,catalog);b=pipeline.request_refresh(report,'evening',root,scope,catalog)
    assert a['request_id']==b['request_id'] and b['reused_request'] and len(calls)==1
    (report/'sources.json').write_text('{"new_day":"SYNTHETIC"}')
    c=pipeline.request_refresh(report,'evening',root,scope,catalog)
    assert c['request_id']!=a['request_id'] and len(calls)==2

def test_pipeline_launch_failure_isolated(tmp_path,monkeypatch):
    report,scope,catalog=fixture_report(tmp_path,monkeypatch)
    def fail(*a,**kw):raise TimeoutError('SYNTHETIC')
    monkeypatch.setattr(pipeline.subprocess,'run',fail)
    r=pipeline.request_refresh(report,'evening',tmp_path/'root',scope,catalog)
    assert r['status']=='refresh_failed' and r['base_report_unblocked']

def test_worker_fails_before_network_on_source_change(tmp_path,monkeypatch):
    report,scope,catalog=fixture_report(tmp_path,monkeypatch);ident=pipeline.identity(report,scope,catalog);key=digest(ident)[:24];root=tmp_path/'root'
    save(root/'requests'/key/'input.json',{'identity':ident,'scope':str(scope),'catalog':str(catalog)})
    (report/'sources.json').write_text('{"changed":1}')
    monkeypatch.setattr(pipeline,'run',lambda *a,**kw:pytest.fail('network before identity gate'))
    assert pipeline.worker(root,key)['status']=='failed'

def test_forward_calendar_gap_not_zero_elapsed(tmp_path):
    from mt1.ta_forward import panel
    r=panel([],{'code':'001309.SZ','market':'CN'},'2026-09-15T20:00:00+08:00','2026-10-15T20:00:00+08:00',tmp_path)
    assert r['status']=='blocked_calendar_gap' and r['missing_dates']

def test_quality_review_identity_and_findings_block(tmp_path,monkeypatch):
    from mt1 import ta_quality as q
    from mt1.ta_research import save
    mp=tmp_path/'manifest.json';mp.write_text('{}')
    save(tmp_path/'input.json',{'stocks':[{'code':'SYNTHETIC','evidence':[{'evidence_id':'e'}]}]})
    auto={'run_id':'r','manifest_hash':'h','input_hash':'i','stocks':[{'code':'SYNTHETIC','result_hash':'rh','errors':[]}]}
    monkeypatch.setattr(q,'automatic',lambda p:auto)
    checks={k:{'status':'pass','rationale':'SYNTHETIC source checked','evidence_ids':['e']} for k in ('facts','short_term','thesis','gaps','B_vs_C')}
    review={k:auto[k] for k in ('run_id','manifest_hash','input_hash')};review.update(reviewer='SYNTHETIC',stocks=[{'code':'SYNTHETIC','result_hash':'rh','status':'pass','checks':checks,'findings':[]}])
    p=tmp_path/'review';p.write_text(json.dumps(review));assert q.checked_review(mp,p)==review
    review['stocks'][0]['findings']=['known_semantic_error'];p.write_text(json.dumps(review))
    with pytest.raises(ValueError,match='conflicts'):q.checked_review(mp,p)
    review['stocks'][0]['status']='blocked';p.write_text(json.dumps(review));assert q.checked_review(mp,p)['stocks'][0]['status']=='blocked'
    review['manifest_hash']='other';p.write_text(json.dumps(review))
    with pytest.raises(ValueError,match='identity'):q.checked_review(mp,p)

def test_forward_panel_matures_twenty_sessions_with_verified_calendar(tmp_path):
    from datetime import datetime,timedelta,timezone
    from mt1.ta_research import evidence
    from mt1.ta_forward import panel
    from mt1.ta_review import evaluate
    start=datetime(2026,1,1,tzinfo=timezone(timedelta(hours=8)))
    days=[{'date':(start+timedelta(days=n)).date().isoformat(),'is_open':True,'close_at':(start+timedelta(days=n,hours=15)).isoformat()} for n in range(23)]
    inputs=[]
    for n,price in [(1,100),(21,110)]:
        raw=tmp_path/str(n);(raw/'inputs').mkdir(parents=True)
        save(raw/'inputs/calendar.json',{'exchange':'SSE','days':days,'source':'SYNTHETIC_ONLY'})
        p=raw/'quote';p.write_text('SYNTHETIC_ONLY')
        t=(start+timedelta(days=n,hours=16)).isoformat()
        ev=evidence('001309.SZ','quote',{'code':'001309.SZ','last':price,'low':price,'high':price,'provider_at':t},'https://test.invalid',t,t,p,currency='CNY',unit='currency_per_share',adjustment='unadjusted_spot',calendar_gate={'allowed':True,'expected_date':days[n]['date']})
        inputs.append(({'run_id':str(n)},{'as_of':t,'stocks':[{'code':'001309.SZ','market':'CN','evidence':[ev]}]}))
    prediction=(start+timedelta(hours=20)).isoformat();asof=(start+timedelta(days=22,hours=20)).isoformat()
    p=panel(inputs,{'code':'001309.SZ','market':'CN'},prediction,asof,tmp_path/'out')
    assert p['status']=='verified_calendar' and '20' in p['observations'] and '40' not in p['observations']
    r=evaluate(prediction,asof,p['sessions'],p['observations'],p['evidence'],[],p['calendar'])
    assert r['horizons'][0]['return']==pytest.approx(.1)
    assert r['business_status']=='blocked' and r['error_categories']['logic']=='unknown'

def test_bounded_repair_is_real_second_request_not_normalization(tmp_path,monkeypatch):
    from mt1 import ta_research as ta
    from test_ta_research import output
    import requests
    p=tmp_path/'raw';p.write_text('SYNTHETIC_ONLY')
    e=ta.evidence('001309.SZ','financial',{'roe':1},'https://test.invalid','2026-09-15T10:00:00+08:00','2026-09-15T11:00:00+08:00',p)
    good=output(e);bad=deepcopy(good);del bad['facts'][0]['numbers'];responses=[bad,good];calls=[]
    class R:
        status_code=200
        def __init__(self,o):self.o=o
        def json(self):return {'id':'synthetic-'+str(len(calls)),'model':ta.MODEL['model'],'choices':[{'message':{'content':json.dumps(self.o)},'finish_reason':'stop'}],'usage':{'prompt_tokens':10,'completion_tokens':20}}
    def post(*a,**kw):calls.append(kw['json']);return R(responses.pop(0))
    monkeypatch.setenv('ANTHROPIC_HUOSHAN_URL','https://ark.cn-beijing.volces.com/api/coding');monkeypatch.setenv('HUOSHAN_API_KEY','SYNTHETIC')
    monkeypatch.setattr(requests,'post',post)
    payload={'as_of':'2026-09-15T20:00:00+08:00','short_target_session':None,'evidence':[e],'frozen_input_hash':'SYNTHETIC'}
    r=ta.call_model(tmp_path/'calls','B',payload,[e])
    assert len(calls)==2 and not r['errors'] and len(r['inference_candidates'])==2
    assert (tmp_path/'calls/B.response.json').read_bytes()==(tmp_path/'calls/B.repair/B.response.json').read_bytes()
    old=json.loads((tmp_path/'calls/B.initial/B.response.json').read_text())
    assert 'numbers' not in json.loads(old['choices'][0]['message']['content'])['facts'][0]
    assert json.loads(calls[1]['messages'][-1]['content'])['contract_repair']['previous_output']==bad
    assert ta.call_model(tmp_path/'calls','B',payload,[e])==r and len(calls)==2

def test_volume_comparison_needs_baseline_not_single_snapshot():
    o=sample();o['facts'][0]['text']='当日成交量放大'
    assert 'volume_comparison_without_baseline' in semantic_errors(o,stock())

def test_cohort_revision_preserves_parent_and_reuses_exact_unchanged_roles(tmp_path,monkeypatch):
    from mt1 import ta_revision as rev,ta_review
    from mt1.ta_research import ROLES,evidence
    from mt1.timing_cli import file_hash
    from test_ta_research import output
    parent=tmp_path/'parent';parent.mkdir();raw=tmp_path/'raw';raw.write_text('SYNTHETIC_ONLY')
    e=evidence('001309.SZ','financial',{'roe':1},'https://test.invalid','2026-09-15T10:00:00+08:00','2026-09-15T11:00:00+08:00',raw)
    good=output(e);good['short_term'].update(trigger='未知，缺短期依据',invalidation='未知，缺短期依据')
    stock={'code':'001309.SZ','name':'SYNTHETIC_ONLY','evidence':[e]};frozen={'stocks':[stock],'as_of':'2026-09-15T20:00:00+08:00'}
    save(parent/'input.json',frozen);calls={}
    def files(directory,role,payload):
        save(directory/(role+'.request.json'),{'messages':[{'content':json.dumps(payload)}]})
        save(directory/(role+'.response.json'),{'synthetic':True,'output':good})
        c={'output':good,'errors':[]};save(directory/(role+'.json'),c);return c
    for role in ROLES:calls[role]=files(parent/stock['code'],role,{'role':role,'evidence':[e]})
    original={'run_id':'SYNTHETIC_PARENT','as_of':frozen['as_of'],'stocks':[{**stock,'calls':calls,'status':'pass','blockers':[]}]}
    save(parent/'results.json',original)
    for s in original['stocks']:save(parent/s['code']/'result.json',s)
    mp=parent/'manifest.json';save(mp,{'run_id':'SYNTHETIC_PARENT','input_hash':digest(frozen),'files':[{'path':str(p.relative_to(parent)),'sha256':file_hash(p)} for p in parent.rglob('*') if p.is_file()]})
    before={str(p):file_hash(p) for p in parent.rglob('*') if p.is_file()}
    fp=tmp_path/'feedback';save(fp,{'manifest_hash':file_hash(mp),'stocks':{'001309.SZ':{'B':['SYNTHETIC correction']}}})
    invoked=[]
    def call(directory,role,payload,es):invoked.append(role);return files(directory,role,payload)
    monkeypatch.setattr(rev,'call_model',call);monkeypatch.setattr(ta_review,'register',lambda *a:None)
    result=rev.revise(mp,fp,tmp_path/'revision');new=Path(result['manifest']).parent
    assert invoked==['B'] and (new/'001309.SZ/C.response.json').read_bytes()==(parent/'001309.SZ/C.response.json').read_bytes()
    assert before=={str(p):file_hash(p) for p in parent.rglob('*') if p.is_file()}
    assert read(new/'input.json')==frozen and read(new/'results.json')['run_id']!='SYNTHETIC_PARENT'
    assert rev.revise(mp,fp,tmp_path/'revision')['reused'] and invoked==['B']

def test_explicit_unknown_volume_is_not_a_volume_claim():
    o=sample();o['facts'][0]['text']='成交量仅有单日数值，无前日或均量对比基准，不能判断放量或缩量'
    assert semantic_errors(o,stock())==[]
    o['facts'][0]['text']='不能判断放量或缩量，但当日成交量放大'
    assert 'volume_comparison_without_baseline' in semantic_errors(o,stock())


def test_consumer_late_quality_failure_resets_consumed_status(tmp_path,monkeypatch):
    from mt1 import ta_consumer as c, ta_audit, ta_quality
    monkeypatch.setattr(c,'read',lambda p: {'manifest':'m','sha256':'hash','quality_review':'q','quality_hash':'bad'} if str(p).endswith('latest.json') else ({'stocks':[],'scope_hash':'hash','as_of':'2026-09-15T20:00:00+08:00','run_id':'SYNTHETIC','scope_epoch':'SYNTHETIC'}))
    monkeypatch.setattr(c,'file_hash',lambda p:'hash')
    monkeypatch.setattr(c,'verify_manifest',lambda p:{'input_hash':'hash'})
    monkeypatch.setattr(c,'digest',lambda x:'hash')
    monkeypatch.setattr(ta_audit,'audit',lambda p:{'stocks':[]})
    monkeypatch.setattr(ta_quality,'automatic',lambda p:{'stocks':[]})
    r=c.consume('2026-09-15T21:00:00+08:00',tmp_path)
    assert r['status']=='research_unavailable' and not r['coverage']
    assert r['reason']=='quality_hash_mismatch'
