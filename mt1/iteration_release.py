"""Atomic experiment pointer only; frozen MT13 ledgers never migrated/revived."""
from pathlib import Path
from .action_loop import read
from .iteration_loop import atomic_json
from .timing import digest
from .timing_cli import file_hash
from .iteration_loop import child, event
from .iteration_validate import validate


def recompute(root, evidence, category, baseline, candidate, asof):
    root=Path(root);kind=read(child(root,'.mt14.json'))['kind']
    evidence=child(root,str(Path(evidence).resolve().relative_to(root.resolve())))
    if kind=='REAL_CURRENT':
        # Arbitrary caller-authored frames/flags are not accepted for real release.
        from .iteration_loop import load_frames
        frames=load_frames(root,verify=True)
        if read(evidence)!=frames:raise ValueError('real_evidence_not_from_committed_runs')
    else:frames=read(evidence)
    return validate(frames,category,baseline,candidate,kind=kind,asof=asof)


def publish(root, evidence, category, baseline, candidate, asof, *, fail_at=None):
    root=Path(root).resolve()
    if category not in ('fundamental','technical'):raise ValueError('invalid_release_category')
    result=recompute(root,evidence,category,baseline,candidate,asof)
    key='release:'+digest(result)
    if result['decision']!='experimental_activate':
        event(root,key,{'decision':result['decision'],'evaluation':result})
        target=child(root,'releases/'+category+'/active.json')
        if result['decision']=='reject' and target.exists() and read(target).get('version')==candidate:
            atomic_json(target,{'version':baseline,'baseline':True,'production':False})
            event(root,key+':risk-rollback',{'decision':'automatic_rollback','reason':'new_forward_evidence_rejected',
                                           'from':candidate,'to':baseline,'retained_ledgers':True})
        return result
    target=child(root,'releases/'+category+'/active.json')
    previous=read(target) if target.exists() else {'version':baseline,'baseline':True}
    h=file_hash(evidence)
    if previous.get('evidence_hash')==h and previous.get('version')==candidate:
        return {**result,'idempotent':True,'pointer':str(target)}
    pointer={'version':candidate,'baseline_version':baseline,'category':category,'kind':read(root/'.mt14.json')['kind'],
             'evidence_path':str(Path(evidence).resolve()),'evidence_hash':h,'evaluation_hash':digest(result),
             'asof':asof,'previous':previous,'production':False}
    intent=child(root,'releases/'+category+'/intents/'+digest(pointer)+'.json')
    atomic_json(intent,pointer)
    event(root,key,{'decision':'prepared','pointer_hash':digest(pointer)})
    if fail_at=='after_prepare':raise RuntimeError('injected_after_prepare')
    atomic_json(target,pointer)
    if fail_at=='after_switch':raise RuntimeError('injected_after_switch')
    event(root,key+':commit',{'decision':'experimental_activate','pointer_hash':digest(pointer)})
    return {**result,'pointer':str(target),'pointer_hash':digest(pointer)}


def recover(root):
    root=Path(root).resolve(); receipts=[]
    for category in ('fundamental','technical'):
        target=child(root,'releases/'+category+'/active.json')
        if not target.exists():
            intents=sorted(child(root,'releases/'+category+'/intents').glob('*.json'),key=lambda p:p.stat().st_mtime_ns)
            if intents:
                p=read(intents[-1])
                try:
                    r=recompute(root,p['evidence_path'],category,p['baseline_version'],p['version'],p['asof'])
                    if file_hash(p['evidence_path'])!=p['evidence_hash'] or digest(r)!=p['evaluation_hash']:
                        raise ValueError('prepared_evidence_changed')
                    atomic_json(target,p);receipts.append({'category':category,'action':'resume_prepared'})
                except (ValueError,OSError,KeyError):
                    receipts.append({'category':category,'action':'discard_invalid_prepared'})
        if not target.exists():continue
        p=read(target)
        if p.get('baseline'):continue
        try:
            if file_hash(p['evidence_path'])!=p['evidence_hash']:raise ValueError('active_evidence_tampered')
            r=recompute(root,p['evidence_path'],category,p['baseline_version'],p['version'],p['asof'])
            if digest(r)!=p['evaluation_hash'] or r['decision']!='experimental_activate':raise ValueError('active_recompute_failed')
        except (ValueError,OSError,KeyError) as exc:
            # Conservative baseline rollback; do not select another unverified candidate.
            fallback={'version':p['baseline_version'],'baseline':True,'production':False}
            atomic_json(target,fallback)
            receipt={'category':category,'action':'automatic_rollback','from':p['version'],'to':fallback['version'],
                     'reason':str(exc),'retained_ledgers':True}
            event(root,'rollback:'+digest(p),receipt);receipts.append(receipt)
    return receipts
