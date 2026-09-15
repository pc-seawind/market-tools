"""Actual revision/consumer regression; no network, model, or publication."""
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT.parent.parent))
from mt1.ta_research import read,save,digest,evidence_errors
from mt1.ta_evidence import verify_collection,coverage
from mt1.timing_cli import file_hash

oldroot=ROOT.parent/'ta10-closeout-20260916'
before=read(oldroot/'collection.json');after=verify_collection(read(ROOT/'collection.json'))
mapping=read(ROOT/'evidence-id-map.json');cfg=read(oldroot/'review-config.json')
assert after['parent_collection_hash']==file_hash(oldroot/'collection.json')
assert len(before['stocks'])==len(after['stocks'])==9
assert len(mapping)==3
unchanged=0;changed=[]
for old,new in zip(before['stocks'],after['stocks']):
 assert old['code']==new['code']
 assert len(old['evidence'])==len(new['evidence'])
 for oe,ne in zip(old['evidence'],new['evidence']):
  assert not evidence_errors(ne,after['as_of'])
  for k in ['raw_path','raw_sha256','text_path','text_sha256','published_at','fetched_at','discovered_at']:
   assert oe[k]==ne[k],k
  if oe['evidence_id'] not in mapping:
   assert oe==ne;unchanged+=1;continue
  assert mapping[oe['evidence_id']]==ne['evidence_id'] and ne['kind']=='institution_forecast'
  b=Path(ne['text_path']).read_text();w=ne['content']['source_window']
  for span in ne['content']['sections']:
   assert w['char_start']<=span['char_start']<span['char_end']<=w['char_end']
   assert span['text']==b[span['char_start']:span['char_end']]
   assert not any(x in span['text'] for x in ['2026.09.14','相关文章','相關文章','會員登入','会员登录'])
  changed.append({'code':new['code'],'evidence_id':ne['evidence_id'],'kind':ne['kind'],'window':w})
 if new['code'] in ['001309.SZ','603986.SH']:
  assert coverage(new,new['tasks'],new['evidence'],cfg,after['as_of'])==new['coverage']
  assert new['coverage']['status']=='blocked' and new['coverage']['quantification_status']=='unknown'
  prior={r['topic']:r['status'] for r in old['coverage']['rows']}
  assert not any(r['status']=='pass' and prior[r['topic']]=='blocked' for r in new['coverage']['rows'])
 else:assert old==new
assert unchanged==53 and len(changed)==3
snapshot=read(ROOT/'protected-before.json')
diff=[p for p,h in snapshot.items() if not Path(p).is_file() or file_hash(p)!=h]
assert not diff,diff
catl=read(ROOT.parent/'ta10-evidence-r2-20260916/final-pointer.json')
assert file_hash(catl['manifest'])==catl['sha256']=='6b5488fc26726ca669190e3a7e257740991ce1ca9be5605ca784feac924bc85c'
proof=[]
for phase in ['morning','weekly']:
 p=ROOT/('consumer-'+phase)/'research-work-inbox.json';rows=read(p)['evidence_supplements']
 latest=next(r for r in rows if r.get('sha256')==file_hash(ROOT/'collection.json'))
 prior=next(r for r in rows if r.get('sha256')==file_hash(oldroot/'collection.json'))
 assert latest['status']=='pending_independent_source_review' and len(latest['stocks'])==9
 assert prior['status']=='superseded_by_source_revision' and latest['sha256'] in prior['superseded_by']
 for s in latest['stocks']:
  stock=next(s0 for s0 in after['stocks'] if s0['code']==s['code'])
  assert s['research_coverage']==stock['coverage']
  assert s['evidence_ids']==[e['evidence_id'] for e in stock['evidence']]
 receipt=read(p.parent/'consumer-receipt.json')
 assert receipt['research_workflow']['sha256']==file_hash(p) and receipt['not_published']
 if phase=='morning':assert receipt['research_refresh']['status']=='skipped_non_evening'
 proof.append({'phase':phase,'path':str(p),'sha256':file_hash(p),'collection_sha256':latest['sha256'],
               'stocks':9,'status':latest['status'],'parent_status':prior['status']})
save(ROOT/'verification.json',{'developer_checks':'pass_not_independent_acceptance','changed':changed,
    'unchanged_evidence':unchanged,'raw_text_pairs_verified':56,'protected_count':len(snapshot),
    'protected_changed':diff,'catl_manifest':catl,'model_calls':0,'network_calls':0,'consumers':proof})
print('PASS',len(snapshot),'protected files; 56 unchanged raw/text pairs; 3 corrected bindings; 53 identical evidence; two consumers')
