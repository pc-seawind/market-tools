"""Synthetic adversarial fixtures, not claimed as real research evidence."""
import copy
from datetime import datetime,timedelta
from pathlib import Path
import pytest
from mt1.calendar import gate,review_deadline,research_snapshot
from mt1.ta_evidence import coverage,proposition_verification,review_lead
from mt1.ta_research import save
from mt1.timing_cli import file_hash


def cal():
 d=datetime.fromisoformat('2026-09-16T00:00:00+08:00')
 return {'exchange':'SSE','source':'SYNTHETIC explicit exchange calendar','fetched_at':'2026-09-15T12:00:00+08:00','days':[{'date':(d-timedelta(days=i)).date().isoformat(),'is_open':i%7 not in (3,4),'close_at':(d-timedelta(days=i)).date().isoformat()+'T15:00:00+08:00'} for i in range(40)]}

@pytest.mark.parametrize('hour,expected',[(0,'2026-09-15'),(9,'2026-09-15'),(14,'2026-09-15'),(16,'2026-09-16')])
def test_last_completed_not_unopened_session(hour,expected):
 now=datetime.fromisoformat(f'2026-09-16T{hour:02d}:10:00+08:00')
 g=gate(cal(),'CN','research',now)
 assert g['allowed'] and g['expected_date']==expected
 if hour<15:assert not gate(cal(),'CN','evening',now)['allowed']

def test_holiday_explicit_not_weekday_guess():
 c=cal();c['days'][0]['is_open']=False
 assert gate(c,'CN','research',datetime.fromisoformat('2026-09-16T19:00:00+08:00'))['expected_date']=='2026-09-15'
 c['days']=c['days'][1:]
 assert not gate(c,'CN','research',datetime.fromisoformat('2026-09-16T00:01:00+08:00'))['allowed']

def test_midnight_bridge_requires_exchange_rows_and_keeps_original():
 c=cal();c['days']=c['days'][1:];old=copy.deepcopy(c)
 n={'code':0,'data':{'fields':['exchange','cal_date','is_open'],'items':[['SSE','20260916',1]]}}
 merged=research_snapshot(c,n,'CN')
 assert c==old and merged!=c
 assert gate(merged,'CN','research',datetime.fromisoformat('2026-09-16T00:01:00+08:00'))['allowed']
 n['data']['items'][0][0]='HKEX'
 with pytest.raises(ValueError):research_snapshot(c,n,'CN')

@pytest.mark.parametrize('now,valid',[('2026-09-16T00:01:00+08:00',True),('2026-09-16T14:00:00+08:00',True),('2026-09-17T00:00:00+08:00',False)])
def test_review_date_is_local_end_of_day(now,valid):
 assert (review_deadline('2026-09-16')>datetime.fromisoformat(now)) is valid

def test_proposition_bound_resolve_unknown_and_wrong_evidence(tmp_path):
 p=tmp_path/'body';p.write_text('公司仅披露总销量，未披露采购结构。')
 e={'evidence_id':'e1','kind':'company_disclosure','text_path':str(p)}
 profile={'propositions':[{'id':'customer','test':'采购影响','requires_quantification':True}]}
 assert proposition_verification(profile,[e],'2026-09-16T00:00:00Z')[0]['status']=='missing'
 r={'status':'unknown','reviewer':'source reviewer','rationale':'总销量不是采购结构','observation_limit':'供应商拆分不可观测','bindings':[{'evidence_id':'e1','text_sha256':file_hash(p),'quote':'公司仅披露总销量'}]}
 profile['proposition_reviews']={'customer':r}
 assert proposition_verification(profile,[e],'2026-09-16T00:00:00Z')[0]['status']=='unknown'
 r['status']='verified'
 assert proposition_verification(profile,[e],'2026-09-16T00:00:00Z')[0]['status']=='verified'
 e['kind']='quote'
 assert proposition_verification(profile,[e],'2026-09-16T00:00:00Z')[0]['status']=='missing'
 e['kind']='company_disclosure';p.write_text('changed')
 assert proposition_verification(profile,[e],'2026-09-16T00:00:00Z')[0]['status']=='missing'

def test_lead_ambiguous_not_admitted(tmp_path):
 p=tmp_path/'body';p.write_text('测试公司')
 r={'status':'ok','text_path':str(p),'raw_path':str(p),'text_sha256':file_hash(p)}
 lead={'url':'https://example.org/story','task_id':'search1'}
 cfg={'lead_publishers':{'example.org':{'kind':'media_report','institution':'Example'}}}
 assert review_lead({'code':'X','name':'测试公司'},lead,r,cfg,'2026-09-16T00:00:00Z')[0] is None

@pytest.mark.parametrize('text,blocked',[
 ('若盈利回落，命题获得支持。',True),
 ('盈利回落不能说明命题获得支持。',False),
 ('不能据此认定命题得到证实。',False),
 ('若采购结构稳定且控制需求价格后盈利改善，则分流损害业绩命题被反驳。',False)])
def test_semantic_support_negation(text,blocked):
 from mt1.ta_quality import semantic_errors
 errors=semantic_errors({'thesis':{'invalidation':text}}, {'evidence':[]})
 assert ('thesis_invalidation_supports_proposition' in errors)==blocked


def test_full_body_lead_admission_not_snippet(tmp_path):
 from mt1.ta_evidence import text_from
 html='<html><head><title>测试公司经营更新</title><meta name="author" content="记者甲"><meta property="article:published_time" content="2026-09-15T19:00:00+08:00"></head><body>测试公司经营更新，全文已读取。</body></html>'
 raw=tmp_path/'raw';raw.write_text(html);body=tmp_path/'text';body.write_text(text_from(raw.read_bytes()))
 r={'status':'ok','raw_path':str(raw),'raw_sha256':file_hash(raw),'text_path':str(body),'text_sha256':file_hash(body),'fetched_at':'2026-09-16T00:00:00Z','discovered_at':'2026-09-16T00:00:00Z'}
 lead={'url':'https://example.org/new-not-in-sources','title':'irrelevant snippet','task_id':'search1'}
 cfg={'lead_publishers':{'example.org':{'kind':'media_report','institution':'Example'}}}
 e,review=review_lead({'code':'X','name':'测试公司'},lead,r,cfg,'2026-09-16T01:00:00Z')
 assert review['status']=='admitted' and e['kind']=='media_report'
 assert e['discovery_task_id']=='search1' and e['author']=='记者甲'
 assert e['published_at']=='2026-09-15T19:00:00+08:00'
 assert e['content']['sections'] and e['text_sha256']==file_hash(body)
 cfg['lead_publishers']['example.org']['kind']='company_disclosure'
 assert review_lead({'code':'X','name':'测试公司'},lead,r,cfg,'2026-09-16T01:00:00Z')[0] is None


def test_unknown_is_not_unsearched_or_publication_approval(tmp_path):
 p=tmp_path/'body';p.write_text('仅总量')
 topics=['news','research','customers','suppliers','competitors','industry','proposition','counterevidence']
 e={'evidence_id':'e1','kind':'company_disclosure','text_path':str(p),'topics':topics}
 cfg={'profiles':{'X':{'propositions':[{'id':'risk','test':'量化采购','requires_quantification':True}],
  'proposition_reviews':{'risk':{'status':'unknown','reviewer':'source-review','rationale':'非采购口径','observation_limit':'供应商拆分未知','bindings':[{'evidence_id':'e1','text_sha256':file_hash(p),'quote':'仅总量'}]}}}}}
 searches=[{'task_id':t,'topic':t,'status':'hits'} for t in topics]
 c=coverage({'code':'X','name':'公司'},searches,[e],cfg,'2026-09-16T00:00:00Z')
 assert c['status']=='pass' and c['quantification_status']=='unknown'
 assert c['publication_status']=='independent_review_required'
 assert coverage({'code':'X','name':'公司'},[],[e],cfg,'2026-09-16T00:00:00Z')['status']=='blocked'


def test_pending_worker_does_not_replace_default_pointer(tmp_path,monkeypatch):
 from mt1 import ta_pipeline as p,ta_evidence,ta_quality
 root=tmp_path/'root';request=root/'requests'/'key'
 save(root/'latest.json',{'reviewed':'SYNTHETIC previous pointer'})
 before=(root/'latest.json').read_bytes()
 task={'identity':{'report':'SYNTHETIC'},'scope':'scope','catalog':'catalog'}
 save(request/'input.json',task)
 monkeypatch.setattr(p,'identity',lambda *a:{})
 monkeypatch.setattr(p,'digest',lambda *a:'key')
 monkeypatch.setattr(ta_evidence,'collect',lambda *a:None)
 mp=root/'revision-manifest';save(mp,{'SYNTHETIC':True})
 monkeypatch.setattr(p,'run',lambda *a,**kw:{'manifest':str(mp),'run_id':'synthetic'})
 monkeypatch.setattr(ta_quality,'automatic',lambda *a:{'status':'review_required'})
 monkeypatch.setattr(p,'due_queue',lambda *a:{})
 assert p.worker(root,'key')['status']=='completed_pending_analyst_review'
 assert (root/'latest.json').read_bytes()==before
 assert (root/'pending.json').exists()
