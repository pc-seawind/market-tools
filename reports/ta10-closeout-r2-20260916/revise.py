"""No network/model calls: immutable correction of three TrendForce bindings."""
import copy,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT.parent.parent))
from mt1.ta_research import read,save,digest,now
from mt1.ta_evidence import admit,coverage,verify_collection
from mt1.timing_cli import file_hash

OLD=ROOT.parent/'ta10-closeout-20260916'
WORK='work_877303d371f64862a748'

def build():
 if (ROOT/'collection.json').exists():raise ValueError('immutable_revision_exists')
 parent=OLD/'collection.json';data=copy.deepcopy(verify_collection(read(parent)))
 cfg=read(OLD/'review-config.json');at=now();changed=[];mapping={}
 urls={
 'https://www.trendforce.cn/presscenter/news/20260703-13133.html':('3 July 2026','若有兴趣'),
 'https://www.trendforce.com.tw/presscenter/news/20260616-13100.html':('16 June 2026','若有興趣')}
 for stock in data['stocks']:
  dirty=False
  for i,old in enumerate(stock['evidence']):
   if old['url'] not in urls:continue
   date,tail=urls[old['url']];body=Path(old['text_path']).read_text()
   date_pos=body.index('\n'+date+'\n');start=body.rfind('\n',0,date_pos)+1
   end=body.index(tail,date_pos)
   spec=copy.deepcopy(old['source_review']['spec'])
   spec.update(kind='institution_forecast',source_window={'char_start':start,'char_end':end,
     'text_sha256':old['text_sha256'],'reviewer':'code-source-boundary-r2-not-independent-acceptance'})
   new,err=admit(stock['code'],spec,{**old,'status':'ok'},data['as_of'])
   assert err is None,err
   new.update(previous_evidence_id=old['evidence_id'],source_review={'reviewer':spec['source_window']['reviewer'],
    'at':at,'spec':spec,'text_sha256':old['text_sha256'],
    'limitation':'全文混合观察与预测，保守整体作为机构展望；不是已实现价格统计，只用于条件推论/背景。6月16日亦含上半年将达及下半年预估。正文之外导航、订阅与后续相关文章均排除。'})
   new['evidence_id']='e_'+digest({k:v for k,v in new.items() if k!='evidence_id'})[:20]
   mapping[old['evidence_id']]=new['evidence_id'];stock['evidence'][i]=new;dirty=True
   changed.append({'code':stock['code'],'old_evidence_id':old['evidence_id'],'evidence_id':new['evidence_id'],
    'url':new['url'],'old_kind':old['kind'],'kind':new['kind'],'raw_sha256':new['raw_sha256'],
    'text_sha256':new['text_sha256'],'source_window':spec['source_window'],'source_review':new['source_review']})
  if dirty:
   # Preserve original review clock/unknown bindings; only classify and bound.
   stock['coverage']=coverage(stock,stock['tasks'],stock['evidence'],cfg,data['as_of'])
   for finding in stock['terminal']['findings']:
    for j,entry in enumerate(finding.get('sources',[])):
     if entry.get('evidence_id') in mapping:
      finding['sources'][j]=next(c for c in changed if c['old_evidence_id']==entry['evidence_id'])
   stock['terminal']['source_boundary_revision']={'at':at,'reason':'预测不得当facts；仅正文可套原发布日期','coverage_not_relaxed':True}
 assert len(changed)==3
 data.update(parent_collection=str(parent.resolve()),parent_collection_hash=file_hash(parent),revision_at=at,
             revision_reason='TrendForce forecast type and article boundary only',model_calls=0,network_calls=0)
 verify_collection(data)
 save(ROOT/'collection.json',data);save(ROOT/'source-review.json',changed)
 save(ROOT/'evidence-id-map.json',mapping)
 save(ROOT/'terminal-nine.json',[{'code':s['code'],'name':s['name'],'coverage':s['coverage'],
      'terminal':s.get('terminal',{'research_status':'R2宁德原样保留/量化unknown'})} for s in data['stocks']])
 pointer={'collection':str((ROOT/'collection.json').resolve()),'sha256':file_hash(ROOT/'collection.json'),
          'parent_collection':str(parent.resolve()),'parent_sha256':file_hash(parent),'work_id':WORK,'not_published':True}
 save(ROOT/'supplement-pointer.json',pointer)
 target=ROOT.parent.parent/'.cron_state/ta10-production/evidence-supplements'/('work_877303d371f64862a748-r2.json')
 assert not target.exists();save(target,pointer)
 print('three corrected bindings; original sources unchanged; new pointer',pointer['sha256'])

if __name__=='__main__':build()
