"""Atomic experiment pointer only; frozen MT13 ledgers never migrated/revived."""
from pathlib import Path
from .action_loop import read
from .iteration_loop import atomic_json
from .timing import digest, instant
from .timing_cli import file_hash
from .iteration_loop import child, event
from .iteration_validate import validate


def recompute(root, evidence, category, baseline, candidate, asof):
    root=Path(root);kind=read(child(root,'.mt14.json'))['kind']
    evidence=child(root,str(Path(evidence).resolve().relative_to(root.resolve())))
    if kind=='REAL_CURRENT':
        # Arbitrary caller-authored frames/flags are not accepted for real release.
        from .iteration_loop import load_frames
        frames=[f for f in load_frames(root,verify=True) if instant(f['observed_at'])<=instant(asof)]
        if read(evidence)!=frames:raise ValueError('real_evidence_not_from_committed_runs')
    else:frames=read(evidence)
    return validate(frames,category,baseline,candidate,kind=kind,asof=asof)


def rollback_target(root, pointer):
    previous=pointer.get('previous',{'version':pointer['baseline_version'],'baseline':True,'production':False})
    while not previous.get('baseline'):
        try:
            result=recompute(root,previous['evidence_path'],previous['category'],previous['baseline_version'],previous['version'],previous['asof'])
            if file_hash(previous['evidence_path'])==previous['evidence_hash'] and digest(result)==previous['evaluation_hash'] and result['decision']=='experimental_activate':
                return previous
        except (ValueError,OSError,KeyError):pass
        previous=previous.get('previous',{'version':previous['baseline_version'],'baseline':True,'production':False})
    return previous


def publish(root, evidence, category, baseline, candidate, asof, *, fail_at=None):
    root=Path(root).resolve()
    if category not in ('fundamental','technical'):raise ValueError('invalid_release_category')
    result=recompute(root,evidence,category,baseline,candidate,asof)
    key='release:'+digest(result)
    if result['decision']!='experimental_activate':
        event(root,key,{'decision':result['decision'],'evaluation':result})
        target=child(root,'releases/'+category+'/active.json')
        if result['decision']=='reject' and target.exists() and read(target).get('version')==candidate:
            fallback=rollback_target(root,read(target))
            atomic_json(target,fallback)
            event(root,key+':risk-rollback',{'decision':'automatic_rollback','reason':'new_forward_evidence_rejected',
                                           'from':candidate,'to':fallback['version'],'retained_ledgers':True})
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
    atomic_json(Path(str(intent)+'.committed'),{'pointer_hash':digest(pointer)})
    return {**result,'pointer':str(target),'pointer_hash':digest(pointer)}


def recover(root):
    root=Path(root).resolve(); receipts=[]
    for category in ('fundamental','technical'):
        target=child(root,'releases/'+category+'/active.json')
        intents=sorted(child(root,'releases/'+category+'/intents').glob('*.json'),key=lambda p:read(p)['asof'])
        for intent in intents:
            committed=Path(str(intent)+'.committed')
            if committed.exists():continue
            p=read(intent)
            current=read(target) if target.exists() else {'version':p['baseline_version'],'baseline':True}
            # Compare-and-swap also protects a later rollback/cancellation from replay.
            if current not in (p,p['previous']):continue
            try:
                r=recompute(root,p['evidence_path'],category,p['baseline_version'],p['version'],p['asof'])
                if file_hash(p['evidence_path'])!=p['evidence_hash'] or digest(r)!=p['evaluation_hash'] or r['decision']!='experimental_activate':
                    raise ValueError('prepared_evidence_changed')
                atomic_json(target,p)
                receipt={'category':category,'action':'resume_prepared','pointer_hash':digest(p)}
                event(root,'recover:'+digest(p),receipt)
                atomic_json(committed,{'pointer_hash':digest(p)})
                receipts.append(receipt)
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
            fallback=rollback_target(root,p)
            atomic_json(target,fallback)
            receipt={'category':category,'action':'automatic_rollback','from':p['version'],'to':fallback['version'],
                     'reason':str(exc),'retained_ledgers':True}
            event(root,'rollback:'+digest(p),receipt);receipts.append(receipt)
    return receipts


def verify_releases(root):
    """Read-only audit. Never call recover or publish from verification."""
    from .iteration_policy import SCHEMA, KINDS
    root=Path(root).resolve()
    identity=read(child(root,'.mt14.json'))
    if identity not in [{'schema':SCHEMA,'kind':k,'production':False} for k in KINDS]:
        raise ValueError('root_identity_changed')
    kind=identity['kind']; checked=[]
    authorized={}
    for manifest in child(root,'runs').glob('*/manifest.json'):
        m=read(manifest)
        if m['kind']!=kind:raise ValueError('run_root_kind_mismatch')
        from .iteration_policy import parameters, CONTRACT
        candidates=read(manifest.parent/'selection.json')['result']['candidates']
        if len(candidates)>CONTRACT['max_candidates'] or len({c['category'] for c in candidates})!=len(candidates):
            raise ValueError('release_candidate_budget_changed')
        for c in candidates:
            cfg=parameters(c['category'],c['parameters'])
            if c['id']!=c['category']+'-'+digest(cfg)[:12] or c['contract_hash']!=digest(CONTRACT):
                raise ValueError('release_candidate_identity_changed')
        for e in read(manifest.parent/'evaluation.json')['result']:
            if not any(c['id']==e['candidate'] and c['category']==e['category'] for c in candidates):
                raise ValueError('release_candidate_not_selected')
            authorized[(e['category'],e['candidate'],e['asof'])]=e
    def pointer(p,category,seen):
        h=digest(p)
        if h in seen:raise ValueError('release_previous_cycle')
        if p.get('baseline'):
            expected='qv-shadow-1' if category=='fundamental' else 'signal-policy-v1'
            if p.get('version')!=expected or p.get('production',False) is not False:
                raise ValueError('release_baseline_forged')
            return
        if p.get('production') is not False:raise ValueError('release_production_forbidden')
        if p.get('category')!=category or p.get('kind')!=kind:raise ValueError('release_category_or_kind_changed')
        if kind=='REAL_CURRENT':
            e=authorized.get((category,p.get('version'),p.get('asof')))
            if e is None or e['baseline']!=p.get('baseline_version'):raise ValueError('release_unknown_candidate')
        ep=child(root,str(Path(p['evidence_path']).relative_to(root)))
        if file_hash(ep)!=p['evidence_hash']:raise ValueError('release_evidence_hash_changed')
        result=recompute(root,ep,category,p['baseline_version'],p['version'],p['asof'])
        if result['decision']!='experimental_activate' or digest(result)!=p['evaluation_hash']:
            raise ValueError('release_evaluation_changed')
        if kind=='REAL_CURRENT' and result!=e:raise ValueError('release_archived_evaluation_changed')
        intent=child(root,'releases/'+category+'/intents/'+h+'.json')
        if not intent.exists() or read(intent)!=p:raise ValueError('release_intent_missing_or_changed')
        committed=Path(str(intent)+'.committed')
        if not committed.exists() or read(committed)!={'pointer_hash':h}:raise ValueError('release_uncommitted_intent')
        events=[read(e) for e in child(root,'events').glob('*.json')]
        if not any(e['key']=='release:'+digest(result) and e['value']=={'decision':'prepared','pointer_hash':h} for e in events):
            raise ValueError('release_prepare_authorization_missing')
        if not any((e['key']=='release:'+digest(result)+':commit' and e['value']=={'decision':'experimental_activate','pointer_hash':h}) or
                   (e['key']=='recover:'+h and e['value']=={'category':category,'action':'resume_prepared','pointer_hash':h}) for e in events):
            raise ValueError('release_commit_event_missing')
        previous=p['previous']
        if not previous.get('baseline') and instant(previous['asof'])>instant(p['asof']):
            raise ValueError('release_previous_time_changed')
        pointer(previous,category,seen|{h})
    for directory in child(root,'releases').glob('*'):
        category=directory.name
        if category not in ('fundamental','technical'):raise ValueError('invalid_release_category')
        intents={}
        for intent in child(root,'releases/'+category+'/intents').glob('*.json'):
            p=read(intent)
            if intent.stem!=digest(p):raise ValueError('release_intent_hash_changed')
            pointer(p,category,set());intents[digest(p)]=p
        for marker in child(root,'releases/'+category+'/intents').glob('*.committed'):
            if not Path(str(marker)[:-10]).exists():raise ValueError('release_orphan_commit')
        active=child(root,'releases/'+category+'/active.json')
        if active.exists():
            p=read(active);pointer(p,category,set())
            # Active must be a committed chain tip or a recorded rollback target.
            tips=set(intents)-{digest(v['previous']) for v in intents.values()}
            if digest(p) not in tips:
                rollbacks=[read(e)['value'] for e in child(root,'events').glob('*.json')]
                if not any(e.get('to')==p['version'] and (e.get('action')=='automatic_rollback' or e.get('decision')=='automatic_rollback') for e in rollbacks):
                    raise ValueError('release_active_commit_mismatch')
            checked.append(category)
        elif intents:raise ValueError('release_active_missing')
    for manifest in child(root,'runs').glob('*/manifest.json'):
        d=manifest.parent;base=child(root,'release-stages/'+d.name)
        if not (base/'complete.json').exists():continue # status reports unfinished transaction
        m=read(manifest);values=[]
        frames=[]
        from .iteration_loop import load_frames
        at=read(d/'inputs.json')['result']['asof']
        frames=[f for f in load_frames(root) if instant(f['observed_at'])<=instant(at)]
        ep=child(root,'evaluations/'+digest(frames)+'.json')
        if read(ep)!=frames:raise ValueError('release_stage_evidence_changed')
        evaluations=read(d/'evaluation.json')['result']
        for c in read(d/'selection.json')['result']['candidates']:
            e=next(e for e in evaluations if e['candidate']==c['id'])
            value={'manifest_hash':file_hash(manifest),'evidence_hash':file_hash(ep),'evaluation':e,'skipped':bool(m['summary']['gaps'])}
            if read(base/(c['category']+'.json'))!=value:raise ValueError('release_stage_changed')
            if not value['skipped']:
                event_path=child(root,'events/'+digest('release:'+digest(e))+'.json')
                if not event_path.exists():raise ValueError('release_event_missing')
                ev=read(event_path)['value']
                if e['decision']!='experimental_activate' and ev!={'decision':e['decision'],'evaluation':e}:
                    raise ValueError('release_event_changed')
            values.append(value)
        if read(base/'complete.json')!={'manifest_hash':file_hash(manifest),'categories_hash':digest(values)}:
            raise ValueError('release_completion_changed')
    return {'identity':identity,'active_categories':checked,'read_only':True}
