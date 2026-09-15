"""Source-reviewed evidence-only revision. Run after bounded receipts exist.

No inference, pointer promotion, trading, or mutation of historical collection.
Metadata/quotes below are developer source review, not independent acceptance.
"""
import copy, json, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT.parent.parent))
from mt1.ta_evidence import admit, coverage, verify_collection, CONFIG
from mt1.ta_research import read, save, now, digest
from mt1.timing_cli import file_hash

REVIEWER='code-source-review-work_877303d371f64862a748-not-independent-acceptance'
PARENT=Path(read(ROOT.parent/'ta10-evidence-r2-20260916/collection-reuse.json')['new'])

def build():
 old=verify_collection(read(PARENT)); cfg=copy.deepcopy(read(CONFIG)); at=now()
 receipt=read(ROOT/'bounded-receipt.json'); sources={**receipt['targeted'],**read(ROOT/'extra-receipt.json')}
 halves=read(ROOT/'half-receipt.json')
 audit=[]; additions={}
 def add(code,key,title,kind,pub,date_token,verify,topics,institution,author,limit,**extra):
  rr=sources[key]
  spec=dict(url=rr['url'],title=title,kind=kind,published_at=pub+'T23:59:59+08:00',
            date_token=date_token,verify=verify,topics=topics,institution=institution,author=author,
            terms=['毛利','库存','價格','价格','成本','AI','cash flow','gross profit','marketing','Food Delivery'],
            origin_key=institution+':'+title,**extra)
  e,err=admit(code,spec,rr,at)
  if err:raise ValueError((code,key,err))
  e['source_review']={'reviewer':REVIEWER,'at':at,'limitation':limit,'spec':spec,
                      'text_sha256':rr['text_sha256'],'scope':'retrieved_body_not_truth_or_independent_signoff'}
  e['evidence_id']='e_'+digest({k:v for k,v in e.items() if k!='evidence_id'})[:20]
  additions.setdefault(code,[]).append(e)
  audit.append({'code':code,'action':'admit','evidence_id':e['evidence_id'],'source_review':e['source_review'],
                'url':e['url'],'raw_sha256':e['raw_sha256'],'text_sha256':e['text_sha256']})
  return e
 for code in ['001309.SZ','603986.SH']:
  add(code,'memory','TrendForce 3Q26价格调查与消费端压力','industry_statistics','2026-07-03','3 July 2026',
      ['TrendForce','NAND','消费'],['industry','counterevidence'],'TrendForce','TrendForce研究团队',
      '7月调查属于较早的Q3展望；预测涨幅不是9月已实现价格，不可映射单一公司库存成本；只补独立行业方向。',background=True)
 add('603986.SH','niche','TrendForce NOR与SLC NAND结构性缺货','industry_statistics','2026-06-16','16 June 2026',
     ['TrendForce','NOR Flash','SLC NAND'],['industry'],'TrendForce','TrendForce研究团队',
     '6月行业背景含预测，不是9月月度利基DRAM已实现价格，不能证明涨价持续。',background=True)
 add('01810.HK','xiaomi_broker','财通证券小米2Q26前瞻公开摘要','institution_forecast','2026-08-13','2026-08-13',
     ['财通证券','郝艳辉','汽车'],['research','counterevidence'],'财通证券','郝艳辉',
     '报告题名日期260812，转载页发布8月13；使用较晚转载日期，前瞻不是实际业绩。仅公开摘要，未取得APP全文。',
     reprint_chain=['财通证券','慧博投研'],publication_precision='reprint_date_not_original_release')
 add('00700.HK','tencent_broker','华兴证券腾讯2Q26点评','institution_forecast','2026-08-18','2026年08月18日',
     ['华兴证券','腾讯','资本开支'],['research','counterevidence'],'华兴证券','华兴证券机构署名（页面未列个人）',
     '公开券商点评，AI投资及未来广告增速为研究估计；机构署名明确，不编造个人作者。',reprint_chain=['华兴证券','新浪财经'])
 add('03690.HK','meituan_broker','华泰证券美团2Q26外卖转盈公开摘要','institution_forecast','2026-08-30','2026-08-30',
     ['华泰证券','夏路路','苏燕妮','邵浩岚'],['research','counterevidence'],'华泰证券','夏路路、苏燕妮、邵浩岚',
     '仅公开摘要；不能把估算UE、目标价、竞争判断当企业事实；APP完整研报未取得。',reprint_chain=['华泰证券','慧博投研'])
 add('03690.HK','jd_peer_pdf','京东2026Q2及中期业绩','company_disclosure','2026-08-13','Aug. 13, 2026',
     ['JD.com','Food Delivery'],['competitors','counterevidence'],'JD.com','JD.com管理层',
     '竞争企业经营披露，非美团自身财报；新业务并非仅外卖，亏损收窄不等于补贴额同口径可比。',
     related_entity_role='competitor',relationship_status='same_food_delivery_market_not_procurement_relation')
 add('01810.HK','xiaomi_primary','小米2026Q2中期业绩原公告','company_disclosure','2026-08-18','August 18, 2026',
     ['Xiaomi','104,199','19.2%'],['company_current','proposition','counterevidence'],'Xiaomi Corporation','公司董事会',
     '汽车、AI及其他创新业务合并分部口径，不把19.2%当纯汽车毛利；自身财报不补第三方覆盖。')
 # Raw HTML supplies exact datePublished; body itself only promises month precision.
 gaming=sources['gaming']; raw=Path(gaming['raw_path']).read_text()
 assert '2026-09-01T00:00' in raw
 add('00700.HK','gaming','Sensor Tower 2026年8月全球手游榜','industry_statistics','2026-09-01','September 2026',
     ['Donny Kristianto','Tencent','third-party Android'],['industry','competitors','counterevidence'],'Sensor Tower','Donny Kristianto',
     '正文月份、HTML datePublished为9月1日；统计期8月，排除第三方安卓市场，流水估算不等于腾讯确认收入或AI投入回报。')
 for code,rr in halves.items():
  key=code+'-half';sources[key]=rr['receipt']
  add(code,key,rr['item']['title'],'company_disclosure',rr['item']['notice_date'][:10],None,
      [code.split('.')[0],'2026','半年度报告'],['company_current','proposition','counterevidence'],
      '德明利' if code=='001309.SZ' else '工业富联','公司董事会',
      '公告日期绑定原始公告索引与详情回执；半报为未经审计期间数据，集团财务不证明逐客户采购或AI订单现金流归因。',
      discovery_index_receipt=rr['detail'])

 # Only substantive already-fetched disclosures are reclassified. Meeting
 # invitations/capital injection notices stay untouched; no blanket topic tags.
 changes={
  'e_4b061cd5c69770061179':(['proposition','counterevidence'],'DRAM产品已历经连续数个季度的价格上涨'),
  'e_a1688f3d60efe68405ac':(['proposition','counterevidence'],'公司在液晶方案的部分产品已批量生产'),
  'e_9e59099a62b16ffb4ee4':(['competitors','counterevidence'],'已实现稳定量产交付'),
  'e_f4436f6db00a5f0f54f4':(['proposition','counterevidence'],'HVLP4 产'),
  'e_14117d66d2cbba49e89a':(['competitors','counterevidence'],'正在客户送样测'),
  'e_5a572265d5d00b613061':(['proposition'],'上半年，公司 CPO 全光交换机已实现样机交货'),
  'e_79c18a7e0cb9366d955f':(['proposition','counterevidence'],'得益於我們即時'),
  'e_bf7644a07be056296ada':(['proposition','counterevidence'],'Capital expenditure was RMB52.8 billion'),
 }
 # Each key proposition stays unknown: real limited observations bound to
 # primary bodies, not missing metadata and not a fabricated causal estimate.
 selected={
 '001309.SZ':('存货余额为 214.12 亿元','已取半报库存与毛利资料，并取得独立价格调查。库存账面增加不能分离价格、数量和结构贡献；后续成本传导及可持续性尚无同口径证据。'),
 '603986.SH':('DRAM产品已历经连续数个季度的价格上涨','管理层披露与TrendForce行业材料补齐，但长期涨价为展望；利基DRAM、NOR、SLC NAND须分品类，未取得9月合约价与客户需求实证。'),
 '688195.SH':('公司在液晶方案的部分产品已批量生产','已披露液晶方案部分量产，其他方案仍验证；不能说完全无订单证据，也不能把计划导入视为已实现客户订单金额。'),
 '301511.SZ':('公司 8 月 HVLP 产品出货量在 500 吨的水平','已披露高端产品出货与良率，同行仍送样提供技术阶段对照；细分加工费、客户接受价及利润增量未独立核实。'),
 '601138.SH':('经营活动产生的现金流量净额变动原因说明','集团回款、现金流与AI出货均有原文，但无法由集团现金流拆出AI订单的回款及逐客户兑现。'),
 '01810.HK':('segment was 19.2%','汽车交付增长与合并创新业务毛利承压并存，已补第三方研报；无法拆分纯汽车/AI成本及供应商采购份额。'),
 '03690.HK':('得益於我們即時','自身盈利改善、华泰公开点评与京东外卖亏损收窄提供反向观察，不能将全部营销费视为补贴；跨平台同口径补贴强度仍未知。'),
 '00700.HK':('Capital expenditure was RMB52.8 billion','已补华兴点评和Sensor Tower手游独立样本，资本开支与游戏广告收入并存不证明因果ROI；手游榜不覆盖第三方安卓也不等同确认收入。'),
 }
 rows=[]
 for prior in old['stocks']:
  stock=copy.deepcopy(prior);code=stock['code']
  if code=='300750.SZ':rows.append(stock);continue
  for i,e in enumerate(stock['evidence']):
   if e['evidence_id'] not in changes:continue
   topics,quote=changes[e['evidence_id']];body=Path(e['text_path']).read_text();assert quote in body
   ne=copy.deepcopy(e);ne.update(previous_evidence_id=e['evidence_id'],topics=list(dict.fromkeys(e.get('topics',[])+topics)),
     source_review={'reviewer':REVIEWER,'at':at,'quote':quote,'text_sha256':e['text_sha256'],
                   'limitation':'只核主体、原文与主题关系；自身或同行声明不是客户独立定点证明。'})
   ne['evidence_id']='e_'+digest({k:v for k,v in ne.items() if k!='evidence_id'})[:20];stock['evidence'][i]=ne
   audit.append({'code':code,'action':'reclassify','old_evidence_id':e['evidence_id'],'evidence_id':ne['evidence_id'],
                 'url':ne['url'],'source_review':ne['source_review'],'raw_sha256':ne['raw_sha256'],'text_sha256':ne['text_sha256']})
  stock['evidence']+=additions.get(code,[])
  query=next(q for q in receipt['queries'] if q['code']==code)
  stock['tasks'].append(query)
  # Explicit reviewed-source retrieval receipts for covered themes. No fake
  # broad search or fake fresh discovery: reused sources identify their parent.
  for topic in ['research','competitors','industry','counterevidence']:
   relevant=[e for e in additions.get(code,[]) if topic in e['topics']]
   if relevant:stock['tasks'].append({'task_id':'closeout-direct-'+code+'-'+topic,'topic':topic,'status':'hits',
     'query':'有界定向正文核查（不是新增关键词搜索）','hits':[{'url':e['url']} for e in relevant],
     'attempts':[{'backend':'archived_public_body','url':e['url'],'text_sha256':e['text_sha256'],'fetched_at':e['fetched_at']} for e in relevant]})
  quote,reason=selected[code]
  candidates=[e for e in stock['evidence'] if e['kind']=='company_disclosure' and quote in Path(e['text_path']).read_text()]
  assert candidates,(code,quote)
  e=candidates[-1]
  review={'status':'unknown','reviewer':REVIEWER,'rationale':reason,'observation_limit':reason,
    'bindings':[{'evidence_id':e['evidence_id'],'url':e['url'],'text_sha256':e['text_sha256'],'quote':quote}]}
  cfg['profiles'][code]['proposition_reviews']={'business_risk':review}
  stock['coverage']=coverage(stock,stock['tasks'],stock['evidence'],cfg,at)
  assert stock['coverage']['quantification_status']=='unknown'
  assert not stock['coverage']['proposition_verification'][0]['errors']
  attempts=[{'receipt':a['receipt'],'backend':a['backend'],'status':a['status']} for a in query['attempts']]
  admitted=[a for a in audit if a['code']==code]
  pending=[{'url':b['lead']['url'],'title':b['lead']['title'],'status':b['receipt']['status'],
            'raw_sha256':b['receipt'].get('raw_sha256'),'text_sha256':b['receipt'].get('text_sha256'),
            'reason':'搜索命中仅为线索；未完成个人作者/原始出处/语义审核，不自动准入'} for b in query['bodies']]
  permissions=[a for a in attempts if a['status']=='access_restricted']
  if code in ('01810.HK','03690.HK'):
   permissions.append({'status':'app_full_text_unobtained','reason':'慧博公开摘要可得；页面要求打开APP阅读全文，未证明付费是唯一途径',
    'url':sources['xiaomi_broker' if code=='01810.HK' else 'meituan_broker']['url']})
  stock['terminal']={'work_status':'bounded_source_work_completed','research_status':'unknown_or_blocked_not_development_failure',
   'findings':[
    {'category':'可取得已补','sources':admitted,'alternative':'原公告索引、发行人IR、券商公开发布页','user_authorization_required':False,'impact':reason},
    {'category':'权限受限','attempts':permissions,'alternative':'已回退公开新闻索引、公开研报摘要及原公告；完整商业数据库仅在确有必要时另行申请',
     'user_authorization_required':'仅后续确需账户或商业资料时；本轮不需要','impact':'受限渠道未绕过，不等于互联网上无证据'},
    {'category':'真实性待核','sources':pending,'alternative':'查原发布机构/具名作者/交易所原文；不使用搜索摘要作事实',
     'user_authorization_required':False,'impact':'待核线索未进入新增事实证据'},
    {'category':'尚无证据','scope':'本轮已检索和冻结材料范围内，不声称全球不存在','gap':reason,'source_attempts':attempts,
     'alternative':'后续同口径经营披露、客户原文、行业价格/经营面板；不要求未公开精确采购份额',
     'user_authorization_required':False,'impact':'不输出确定因果、精确份额、盈利归因或收益认证'}]}
  rows.append(stock)
 out={**old,'stocks':rows,'as_of':at,'parent_collection_hash':file_hash(PARENT),'parent_collection':str(PARENT),
      'revision_at':at,'work_id':'work_877303d371f64862a748','model_calls':0,
      'identity':{**old['identity'],'review_config_hash':digest(cfg)},
      'review_scope':'evidence_only_source_review_not_model_or_independent_acceptance'}
 verify_collection(out)
 assert next(s for s in rows if s['code']=='300750.SZ')==next(s for s in old['stocks'] if s['code']=='300750.SZ')
 save(ROOT/'review-config.json',cfg);save(ROOT/'source-review.json',audit);save(ROOT/'collection.json',out)
 save(ROOT/'terminal-nine.json',[{'code':s['code'],'name':s['name'],'evidence_count':len(s['evidence']),
  'coverage':s['coverage'],'terminal':s.get('terminal',{'category':'可取得已补（R2复用）','research_status':'采购量化unknown',
   'model_run_unchanged':True,'no_new_source_or_model_work':True,'source':'../ta10-evidence-r2-20260916/final-pointer.json'})} for s in rows])
 print('source_reviewed',len(audit),'stocks',len(rows),'model_calls',0)

if __name__=='__main__':
 if (ROOT/'collection.json').exists():raise SystemExit('immutable closeout already exists; use a new directory for a new revision')
 build()
