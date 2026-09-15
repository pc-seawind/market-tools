"""Targeted, immutable cohort revision after source-by-source analyst review.
Unchanged roles are exact byte reuse of the SAME frozen cohort, not new calls.
Changed roles always call the provider; initial/repair/parent records retained.
"""
import argparse
import fcntl
import shutil
from pathlib import Path
from .ta_research import read,save,digest,now,verify_manifest,call_model,ROLES
from .ta_audit import strict_output
from .ta_quality import semantic_errors
from .timing_cli import file_hash
from .data import atomic_json
from .ta_dependencies import DEPS, equivalent, check


def revise(parent_manifest,feedback_path,root):
    parent=Path(parent_manifest);m=verify_manifest(parent);feedback=read(feedback_path);frozen=read(parent.parent/'input.json');original=read(parent.parent/'results.json')
    if feedback['manifest_hash']!=file_hash(parent):raise ValueError('feedback_parent_mismatch')
    if any(c not in {s['code'] for s in frozen['stocks']} for c in feedback['stocks']):raise ValueError('foreign_repair_stock')
    key=digest({'parent':file_hash(parent),'feedback':feedback,'revision_code':file_hash(__file__)})[:24]
    rid='ta10-revision-'+key;root=Path(root);root.mkdir(parents=True,exist_ok=True)
    with (root/'.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        out=root/'runs'/rid;out.mkdir(parents=True,exist_ok=True);mp=out/'manifest.json'
        if mp.exists():verify_manifest(mp);return {'run_id':rid,'manifest':str(mp),'manifest_hash':file_hash(mp),'reused':True}
        save(out/'input.json',frozen);save(out/'analyst-feedback.json',feedback)
        stocks=[]
        for stock in frozen['stocks']:
            code=stock['code'];src=parent.parent/code;dest=out/code;dest.mkdir(exist_ok=True);calls={}
            changes=dict(feedback['stocks'].get(code,{}));dependency_records={}
            old=next(s for s in original['stocks'] if s['code']==code)
            if old.get('status')=='not_run':
                if changes:raise ValueError('unrun_stock_requires_first_inference_not_revision')
                save(dest/'result.json',old);stocks.append(old);continue
            if any(r not in ROLES for r in changes):raise ValueError('invalid_repair_role')
            for role in ROLES:
                import json
                parent_payload=json.loads(read(src/(role+'.request.json'))['messages'][-1]['content'])
                if role in DEPS:
                    # Any actual argument difference invalidates all dependants.
                    stale,record=check(role,parent_payload,{r:calls[r].get('output') for r in DEPS[role]})
                    dependency_records[role]=record
                    if stale and role not in changes:changes[role]=['dependency_invalidated:'+','.join(stale)]
                if role not in changes:
                    for p in src.glob(role+'.*'):
                        target=dest/p.name
                        if p.is_dir():shutil.copytree(p,target,dirs_exist_ok=True)
                        elif not target.exists():target.write_bytes(p.read_bytes())
                    calls[role]=read(dest/(role+'.json'))
                    continue
                # Retain old output separately, never patch it into compliance.
                for p in src.glob(role+'.*'):
                    target=out/'parent-records'/code/p.name;target.parent.mkdir(parents=True,exist_ok=True)
                    if p.is_dir():shutil.copytree(p,target,dirs_exist_ok=True)
                    elif not target.exists():target.write_bytes(p.read_bytes())
                import json
                payload=json.loads(read(src/(role+'.request.json'))['messages'][-1]['content'])
                for k in ('role','numeric_catalog','final_schema_reminder','contract_repair'):payload.pop(k,None)
                payload['analyst_source_review']={'issues':changes[role],'previous_own_output':read(src/(role+'.json'))['output'],
                    'instruction':'按原文纠正这些具体问题并重新生成完整JSON；保留真实风险，不能仅删除不利事实。不得增补证据之外的信息。'}
                if role.endswith('_cross'):payload['initial_arguments']={r:calls[r].get('output') for r in ('bull','bear')}
                if role=='C':payload['debate']={r:calls[r].get('output') for r in ('bull','bear','bull_cross','bear_cross')}
                calls[role]=call_model(dest,role,payload,stock['evidence'])
            old=next(s for s in original['stocks'] if s['code']==code)
            errors=[role+':'+e for role,c in calls.items() for e in sorted(set(c['errors']+strict_output(c.get('output'),stock['evidence'])+semantic_errors(c.get('output'),stock)))]
            errors += [e for e in old.get('blockers',[]) if e in ('quote_missing','current_company_evidence_missing')]
            result={**old,'calls':calls,'status':'blocked' if errors else 'pass','blockers':errors,'parent_manifest_hash':file_hash(parent),'changed_roles':list(changes),'parent_dependency_comparison':dependency_records,'unchanged_roles':'byte_reused_same_frozen_input_NOT_new_model_calls'}
            save(dest/'result.json',result);stocks.append(result)
        save(out/'results.json',{**original,'run_id':rid,'stocks':stocks,'available_at':now(),'parent_manifest_hash':file_hash(parent),'revision_policy':'same_frozen_nine_stock_cohort_selective_real_model_repair','not_published':True})
        files=[{'path':str(p.relative_to(out)),'sha256':file_hash(p)} for p in sorted(out.rglob('*')) if p.is_file()]
        save(mp,{'run_id':rid,'input_hash':digest(frozen),'files':files,'archived_at':now(),'parent_manifest':str(parent.resolve()),'parent_manifest_hash':file_hash(parent),'semantic_review':'pending_independent_review','dependency_contract':'schema_equivalence_v1'})
        from .ta_review import register
        register(mp);atomic_json(root/'latest.json',{'manifest':str(mp.resolve()),'sha256':file_hash(mp),'run_id':rid})
        return {'run_id':rid,'manifest':str(mp),'manifest_hash':file_hash(mp)}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--parent',required=True);p.add_argument('--feedback',required=True);p.add_argument('--root',required=True);a=p.parse_args()
    import json
    print(json.dumps(revise(a.parent,a.feedback,a.root),ensure_ascii=False))
