"""Evidence-backed method lifecycle. Only explicit promotion, never auto-active."""
STAGES = ['candidate','shadow','validated','active']


def reduce_method(old, patch):
    new={**(old or {}),**patch}
    for k in ('method_id','rule_version','sources','rules','parameters','horizon','rollback','status','change_reason'):
        if not new.get(k): raise ValueError('method field missing: '+k)
    if new['status'] not in STAGES: raise ValueError('invalid method status')
    if not old and new['status']!='candidate': raise ValueError('start as candidate')
    if old:
        if new.get('registration_kind') != old.get('registration_kind'): raise ValueError('immutable registration kind')
        if new['method_id']!=old['method_id']: raise ValueError('immutable method id')
        changed=any(new[k]!=old[k] for k in ('rule_version','rules','parameters'))
        if changed and new['status']!='candidate': raise ValueError('changed rules require candidate reset')
        if STAGES.index(new['status']) > STAGES.index(old['status'])+1: raise ValueError('no skipped promotion')
    if old and old.get('registration_kind') == 'existing_production_baseline':
        if set(patch) - {'status','change_reason'} or STAGES.index(new['status']) > STAGES.index(old['status']):
            raise ValueError('baseline only supports withdrawal; changed rules use new candidate')
        return new
    for s in new['sources']:
        if not all(s.get(k) for k in ('url','version','published_date','fetched_at','content_hash')) or s.get('fetch_status')!='ok':
            raise ValueError('source fetch not verified')
    if new['status'] in ('validated','active'):
        t=new.get('validation') or {}
        if not (t.get('artifact_hash') and t.get('rule_version')==new['rule_version'] and t.get('exact_channels') is True
                and t.get('point_in_time') is True and t.get('oos_pass') is True and t.get('independent_samples',0)>=50
                and set(t.get('horizons',[]))=={20,40,60} and t.get('reviewer')):
            raise ValueError('validation insufficient; remain shadow')
    return new


def register_existing(store, root, reviewer):
    """Explicit, hash-pinned baseline migration; NOT validation of a new factor.

    No user supplied rules/paths: only the existing production candidate pipeline.
    A changed source produces a new version/id, never silently upgrades old plans.
    """
    import hashlib
    from pathlib import Path
    from datetime import datetime, timezone
    from .store import digest
    root = Path(root)
    sources=[]
    for name in ('sector_picks.py','sector_score.py','grading.py'):
        path=root/name
        sources.append({'path':str(path),'content_hash':hashlib.sha256(path.read_bytes()).hexdigest()})
    if not reviewer: raise ValueError('baseline migration reviewer required')
    version=digest(sources)
    method_id='existing-production-'+version[:16]
    payload={'method_id':method_id,'rule_version':version,'status':'active',
             'registration_kind':'existing_production_baseline', 'sources':sources,
             'rules':{'source_files':[s['path'] for s in sources]},
             'parameters':{'unchanged':True},'horizon':[20,40,60],
             'rollback':'shadow','change_reason':'显式登记原生产规则，不引入新因子，不声明回测通过',
             'reviewer':reviewer,'validation_status':'not_revalidated',
             'registered_at':datetime.now(timezone.utc).isoformat()}
    existing=store.latest('method:'+method_id)
    if existing:return existing  # Never reactivate a withdrawn baseline on retry.
    return store.apply('method:'+method_id,0,'baseline:'+method_id,payload,
                       payload['change_reason'],lambda old,p:p)
