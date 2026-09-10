"""Evidence-backed method lifecycle. Only explicit promotion, never auto-active."""
STAGES = ['candidate','shadow','validated','active']


def reduce_method(old, patch):
    new={**(old or {}),**patch}
    for k in ('method_id','rule_version','sources','rules','parameters','horizon','rollback','status','change_reason'):
        if not new.get(k): raise ValueError('method field missing: '+k)
    if new['status'] not in STAGES: raise ValueError('invalid method status')
    if not old and new['status']!='candidate': raise ValueError('start as candidate')
    if old:
        if new['method_id']!=old['method_id']: raise ValueError('immutable method id')
        changed=any(new[k]!=old[k] for k in ('rule_version','rules','parameters'))
        if changed and new['status']!='candidate': raise ValueError('changed rules require candidate reset')
        if STAGES.index(new['status']) > STAGES.index(old['status'])+1: raise ValueError('no skipped promotion')
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
