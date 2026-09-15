"""Read-only actual-artifact verification, no inference or acceptance signoff."""
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT.parent.parent))
from mt1.ta_research import read,save,digest,evidence_errors
from mt1.ta_evidence import verify_collection,coverage
from mt1.timing_cli import file_hash

data=verify_collection(read(ROOT/'collection.json'))
parent=read(data['parent_collection']); cfg=read(ROOT/'review-config.json')
assert data['parent_collection_hash']==file_hash(data['parent_collection'])
assert {s['code'] for s in data['stocks']}=={s['code'] for s in parent['stocks']}
assert len(data['stocks'])==9 and data['model_calls']==0
checked=[]
for stock in data['stocks']:
 for e in stock['evidence']:assert not evidence_errors(e,data['as_of']),(e['evidence_id'],evidence_errors(e,data['as_of']))
 if stock['code']=='300750.SZ':
  assert stock==next(s for s in parent['stocks'] if s['code']==stock['code'])
 else:
  assert coverage(stock,stock['tasks'],stock['evidence'],cfg,data['as_of'])==stock['coverage']
  assert stock['coverage']['quantification_status']=='unknown'
  assert stock['coverage']['status']=='blocked'
  for v in stock['coverage']['proposition_verification']:assert not v['errors'] and v['bindings']
 checked.append({'code':stock['code'],'evidence_count':len(stock['evidence']),
                 'coverage_hash':digest(stock['coverage']),'status':stock['coverage']['status']})
ptr=read(ROOT.parent/'ta10-evidence-r2-20260916/final-pointer.json')
assert file_hash(ptr['manifest'])==ptr['sha256']=='6b5488fc26726ca669190e3a7e257740991ce1ca9be5605ca784feac924bc85c'
protections=[]
for name in ['protected-before.json','consumer-protected-before.json']:
 before=read(ROOT/name)
 changed=[p for p,h in before.items() if not Path(p).is_file() or file_hash(p)!=h]
 assert not changed,changed
 protections.append({'snapshot':name,'checked':len(before),'changed':changed})
consumers=[]
for phase in ['morning','weekly']:
 path=ROOT/('consumer-'+phase)/'research-work-inbox.json'; inbox=read(path)
 rows=[r for r in inbox['evidence_supplements'] if r.get('work_id')==data['work_id']]
 assert len(rows)==1 and rows[0]['status']=='pending_independent_source_review'
 assert rows[0]['sha256']==file_hash(ROOT/'collection.json')
 assert rows[0]['does_not_replace_frozen_model_evidence'] and rows[0]['not_published']
 for s in rows[0]['stocks']:
  original=next(x for x in data['stocks'] if x['code']==s['code'])
  assert s['research_coverage']==original['coverage']
  assert s['evidence_ids']==[e['evidence_id'] for e in original['evidence']]
 receipt=read(path.parent/'consumer-receipt.json')
 assert receipt['research_workflow']['sha256']==file_hash(path)
 assert receipt['not_published'] and receipt['production_schedule_unchanged']
 if phase=='morning':assert receipt['research_refresh']['status']=='skipped_non_evening'
 consumers.append({'phase':phase,'path':str(path),'sha256':file_hash(path),'supplement':rows[0]['sha256'],
                   'stocks':len(rows[0]['stocks']),'status':rows[0]['status'],'not_published':True})
save(ROOT/'consumer-proof.json',consumers)
save(ROOT/'verification.json',{'status':'developer_artifact_checks_pass_not_independent_acceptance',
     'stocks':checked,'catl_manifest':ptr,'protections':protections,'consumers':consumers,'model_calls':0})
print('PASS: nine stocks, eight hash-bound unknown propositions, CATL unchanged, both actual consumers; no inference')
