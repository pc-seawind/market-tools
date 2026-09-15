import copy
from pathlib import Path
import pytest
from mt1.ta_evidence import task_plan,coverage,numeric_extract,admit,metric_errors
from mt1.ta_numeric import validate_number,conflicts
from mt1.ta_research import validate_output

NOW='2026-09-16T10:00:00+00:00'
def spec():
 return {'field':'share','pattern':r'份额(?P<value>39\.9)%','subject':'CATL','metric':'share','unit':'percent','currency':'not_applicable','period_start':'2026-01-01','period_end':'2026-07-31','scope':'global EV','basis':'cumulative_installation','is_forecast':False}

def numeric(tmp_path):
 body='CATL 全球累计装车份额39.9%';p=tmp_path/'body';p.write_text(body)
 metrics=numeric_extract(body,[spec()],'industry_statistics',NOW)
 e={'evidence_id':'e1','kind':'industry_statistics','text_path':str(p),'published_at':NOW,'content':{'numeric_metrics':metrics}}
 n={'evidence_id':'e1',**metrics['share']}
 return body,e,n

def test_selector_requires_unique_locator(tmp_path):
 body,e,n=numeric(tmp_path)
 assert not validate_number(n,e)
 assert numeric_extract(body+body,[spec()],'industry_statistics',NOW)=={}
 assert n['page']==1 and n['formula']=='raw_value * 1'

@pytest.mark.parametrize('key,value,error',[('unit','GWh','unit_currency_conflict'),('subject','BYD','numeric_subject_conflict'),('period_end','2026-08-31','numeric_period_end_conflict'),('basis','monthly_shipments','numeric_basis_conflict'),('scope','China','numeric_scope_conflict'),('value','40.0','numeric_conflict'),('is_forecast',True,'numeric_is_forecast_conflict')])
def test_semantic_dimensions_fail_closed(tmp_path,key,value,error):
 _,e,n=numeric(tmp_path);n[key]=value
 assert error in validate_number(n,e)

def test_future_actual_and_forecast(tmp_path):
 body,e,n=numeric(tmp_path)
 n['period_end']='2027-01-01'
 assert 'future_actual_period' in metric_errors(n,body,'industry_statistics',NOW)
 n['is_forecast']=True
 assert not metric_errors(n,body,'institution_forecast',NOW)

def test_catalog_locator_tamper(tmp_path):
 _,e,n=numeric(tmp_path);Path(e['text_path']).write_text('changed')
 assert 'locator_mismatch' in validate_number(n,e)

def test_conflicting_sources_never_averaged(tmp_path):
 _,e,n=numeric(tmp_path);other=copy.deepcopy(e);other['evidence_id']='e2';other['content']['numeric_metrics']['share']['value']='38.0'
 assert len(conflicts([e,other]))==1

@pytest.mark.parametrize('kind',['institution_forecast','media_report','market_narrative'])
def test_nonfacts_not_facts(kind):
 o={'facts':[{'text':'确定事实','evidence_ids':['e1'],'numbers':[]}]}
 assert 'nonfact_source_as_fact' in validate_output(o,[{'evidence_id':'e1','kind':kind,'content':{}}])

def test_propositions_generic_and_news_windows():
 stock={'code':'X','name':'测试公司'};cfg={'profiles':{'X':{'industry':'铜箔','propositions':[{'id':'cost','query':'加工费下降','counter_query':'加工费上涨','test':'毛利传导'}]}}}
 ts=task_plan(stock,cfg,NOW)
 assert {t['window_days'] for t in ts if t['topic']=='news'}=={7,30}
 assert any(t['proposition_id']=='cost' and t['topic']=='counterevidence' for t in ts if 'proposition_id' in t)
 c=coverage(stock,[],[],cfg,NOW)
 assert c['status']=='blocked' and c['rows'][0]['reason']=='not_searched'
 assert all(r['owner'] and r['next_check_at'] and r['remedy'] for r in c['rows'])

def test_many_mirrors_do_not_cover_primary_risk():
 stock={'code':'X','name':'测试'};cfg={'profiles':{'X':{}}}
 search=[{'task_id':'1','topic':'customers','status':'hits'}]
 es=[{'kind':'media_report','topics':['customers'],'evidence_id':str(i)} for i in range(10)]
 c=coverage(stock,search,es,cfg,NOW)
 assert next(r for r in c['rows'] if r['topic']=='customers')['status']=='blocked'

def test_future_publication_not_admitted(tmp_path):
 p=tmp_path/'body';p.write_text('原文')
 r={'status':'ok','text_path':str(p)}
 assert admit('X',{'published_at':'2027-01-01T00:00:00Z'},r,NOW)[1]=='future_publication'


def test_conversion_keeps_raw_and_formula():
 s=spec();s.update(conversion_factor='0.01',unit='fraction')
 n=numeric_extract('份额39.9%',[s],'industry_statistics',NOW)['share']
 assert n['raw_value']=='39.9' and n['value']=='0.399' and n['formula']=='raw_value * 0.01'


def test_equity_subject_difference_not_numeric_conflict(tmp_path):
 _,e,n=numeric(tmp_path);other=copy.deepcopy(e)
 other['content']['numeric_metrics']['share'].update(subject='indirect_parent',value='11.17')
 assert conflicts([e,other])==[]


def test_fetch_fallback_and_immutable_replay(tmp_path,monkeypatch):
 import requests
 from mt1.ta_evidence import fetch
 class Response:
  encoding='utf-8'
  def __init__(self,status,body):self.status_code=status;self.body=body
  def __enter__(self):return self
  def __exit__(self,*a):pass
  def iter_content(self,*a):yield self.body
 calls=[]
 def get(url,**kw):
  calls.append(url)
  return Response(403,b'denied') if len(calls)==1 else Response(200,('<p>原文正文资料，不能视为付费权限已获授权。'+('公开资料。'*20)+'</p>').encode())
 monkeypatch.setattr(requests,'get',get)
 r=fetch('https://example.org/source',tmp_path/'fetch')
 assert r['status']=='ok' and len(r['attempts'])==2
 assert fetch('https://example.org/source',tmp_path/'fetch')==r and len(calls)==2
 Path(r['raw_path']).write_bytes(b'tampered')
 with pytest.raises(ValueError,match='cached_source_changed'):fetch('https://example.org/source',tmp_path/'fetch')


def test_search_no_results_distinct_from_restricted(tmp_path,monkeypatch):
 from mt1 import ta_evidence as t
 p=tmp_path/'raw';p.write_text('<rss><channel></channel></rss>')
 task={'task_id':'t','query':'test','published_after':NOW,'published_before':NOW}
 monkeypatch.setattr(t,'fetch',lambda *a,**k:{'status':'ok','discovered_at':NOW,'raw_path':str(p)})
 assert t.search(task,tmp_path,backends=['bing_rss'])['status']=='searched_no_results'
 monkeypatch.setattr(t,'fetch',lambda *a,**k:{'status':'access_restricted','discovered_at':NOW})
 assert t.search(task,tmp_path,backends=['bing_rss'])['status']=='access_restricted'


def test_collect_checkpoint_and_changed_plan(tmp_path,monkeypatch):
 from mt1 import ta_evidence as t
 from mt1.ta_research import save
 scope=tmp_path/'scope.json';save(scope,{'confirmed_holdings':[{'code':'X','name':'test'}]})
 cfg=tmp_path/'config.json';save(cfg,{'profiles':{'X':{}}})
 calls=[]
 def search(task,*a,**kw):
  calls.append(task);return {**task,'status':'searched_no_results','hits':[],'attempts':[]}
 monkeypatch.setattr(t,'search',search)
 monkeypatch.setattr(t,'report_discovery',lambda *a:([],{'task_id':'broker-index-X','topic':'research','status':'searched_no_results','hits':[]}))
 monkeypatch.setattr(t,'announcement_discovery',lambda *a:([],{'task_id':'ann-index-X','topic':'announcement_search','status':'searched_no_results','hits':[]}))
 r=t.collect(tmp_path/'run',scope,cfg);count=len(calls)
 assert t.collect(tmp_path/'run',scope,cfg)==r and count==len(calls)
 cfg.write_text('{"profiles":{"X":{}},"news_days":[14]}')
 with pytest.raises(ValueError,match='new_collection_revision_required'):t.collect(tmp_path/'run',scope,cfg)


def test_invalid_json_gets_one_real_repair_not_local_edit(tmp_path,monkeypatch):
 from mt1 import ta_research as t
 from mt1 import ta_audit,ta_quality
 from mt1.timing_cli import file_hash
 calls=[]
 def once(directory,role,payload,es):
  directory=Path(directory);directory.mkdir(parents=True)
  request={'payload':payload};response={'choices':[{'message':{'content':'{broken' if not calls else '{}'}}]}
  t.save(directory/(role+'.request.json'),request);t.save(directory/(role+'.response.json'),response)
  calls.append(payload)
  return {'request_hash':t.digest(request),'response_hash':file_hash(directory/(role+'.response.json')),'errors':['invalid_json'] if len(calls)==1 else [],'output':None if len(calls)==1 else {}}
 monkeypatch.setattr(t,'_call_once',once);monkeypatch.setattr(ta_audit,'strict_output',lambda *a:[]);monkeypatch.setattr(ta_quality,'semantic_errors',lambda *a:[])
 r=t.call_model(tmp_path/'role','B',{},[])
 assert len(calls)==2 and calls[1]['contract_repair']['previous_raw_text_if_invalid_json']=='{broken'
 assert r['output']=={} and not r['errors']
 assert (tmp_path/'role/B.response.json').read_bytes()==(tmp_path/'role/B.repair/B.response.json').read_bytes()


def test_revision_preserves_explicit_not_run_stock(tmp_path):
 from mt1.ta_research import save,digest
 from mt1.ta_revision import revise
 from mt1.timing_cli import file_hash
 frozen={'stocks':[{'code':'X','evidence':[]}]}
 parent=tmp_path/'parent';save(parent/'input.json',frozen)
 skipped={'code':'X','status':'not_run','calls':{},'blockers':['model_not_run']}
 save(parent/'X/result.json',skipped);save(parent/'results.json',{'run_id':'parent','stocks':[skipped]})
 save(parent/'manifest.json',{'run_id':'parent','input_hash':digest(frozen),'files':[{'path':str(p.relative_to(parent)),'sha256':file_hash(p)} for p in parent.rglob('*.json')]})
 feedback=tmp_path/'feedback.json';save(feedback,{'manifest_hash':file_hash(parent/'manifest.json'),'stocks':{}})
 # register needs as_of for a real inference; no inference here, isolate registry.
 import unittest.mock
 with unittest.mock.patch('mt1.ta_review.register'):
  r=revise(parent/'manifest.json',feedback,tmp_path/'revision')
 assert __import__('json').load(open(Path(r['manifest']).parent/'X/result.json'))==skipped


def test_failed_json_inference_is_still_counted(tmp_path):
 from mt1.ta_research import save
 from mt1.ta_audit import model_usage
 call={'usage':{'prompt_tokens':100,'completion_tokens':20},'attempts':[{'status':200,'elapsed_seconds':2}],'errors':['invalid_json'],'output':None}
 save(tmp_path/'results.json',{'stocks':[{'code':'X','calls':{'B':call}}]})
 u=model_usage(tmp_path/'manifest.json')
 assert u['calls']==1 and u['prompt_tokens']==100 and u['new_calls']==1


def test_support_condition_is_not_thesis_invalidation():
 from mt1.ta_quality import semantic_errors
 o={'short_term':{'trigger':'未知','invalidation':'未知'},'thesis':{'invalidation':'若利润下降则客户分流命题获得支持线索'}}
 assert 'thesis_invalidation_supports_proposition' in semantic_errors(o,{'evidence':[]})
 o['thesis']['invalidation']='若客户原始采购份额持续增长，则分流命题未获支持'
 assert 'thesis_invalidation_supports_proposition' not in semantic_errors(o,{'evidence':[]})


def test_freeze_refuses_new_collection_in_old_root(tmp_path):
 from mt1.ta_research import save,freeze,MODEL
 from mt1.timing_cli import file_hash
 scope=tmp_path/'scope';scope.write_text('{}');collection=tmp_path/'collection';collection.write_text('{}')
 save(tmp_path/'root/input.json',{'model':MODEL,'scope_hash':file_hash(scope),'research_collection_hash':'old','stocks':[]})
 with pytest.raises(ValueError,match='research_collection_changed_new_run_required'):
  freeze(tmp_path/'root',tmp_path,scope,research_collection=collection)
