"""MT14 single run/status/verify/demo entrypoint, isolated durable stages."""
import fcntl
import json
import os
from contextlib import contextmanager
from pathlib import Path
from .data import atomic_json
from .action_loop import now, read
from .timing import digest
from .timing_cli import file_hash
from .iteration_policy import SCHEMA, KINDS, CONTRACT


def init(root, kind):
    root = Path(root).resolve()
    if kind not in KINDS or '.cron_state' in root.parts or 'investment' in root.parts:
        raise ValueError('unsafe_experiment_root_or_kind')
    root.mkdir(parents=True, exist_ok=True)
    marker = root/'.mt14.json'
    if marker.exists():
        if read(marker) != {'schema': SCHEMA, 'kind': kind, 'production': False}:
            raise ValueError('root_identity_changed')
    else:
        if any(root.iterdir()):
            raise ValueError('refuse_adopt_nonempty_root')
        atomic_json(marker, {'schema': SCHEMA, 'kind': kind, 'production': False})
    return root


def child(root, relative):
    root=Path(root).resolve(); p=root/relative
    if not (root/'.mt14.json').is_file() or p.is_symlink() or not p.resolve().is_relative_to(root):
        raise ValueError('outside_isolated_root')
    # Do not follow even an internal symlink: path ownership must be unambiguous.
    if any(x.is_symlink() for x in [p, *p.parents] if x != root and x.is_relative_to(root)):
        raise ValueError('symlink_refused')
    return p


@contextmanager
def locked(root):
    with child(root, '.iteration.lock').open('a') as f:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def event(root, key, value):
    path=child(root, 'events/'+digest(key)+'.json')
    record={'key':key, 'value':value, 'sha256':digest(value)}
    if path.exists():
        if read(path)!=record: raise ValueError('idempotency_key_conflict')
        return record
    atomic_json(path, record)
    return record


def stage(root, run_dir, name, compute):
    p=child(root, str(run_dir.relative_to(root)/name)+'.json')
    if p.exists():
        v=read(p)
        if digest(v['result'])!=v['sha256']:raise ValueError('stage_tampered')
        return v['result']
    result=compute()
    atomic_json(p, {'result':result, 'sha256':digest(result)})
    event(root, str(p.relative_to(root)), {'stage':name,'sha256':digest(result)})
    return result


def propose(fund, tech, effective_at):
    """One prespecified tightening per engine; no cherry-picked parameter grid."""
    from .candidates import screen
    from .action_loop import decide, POLICY
    from .iteration_policy import candidate
    choices=[]; reasons=[]
    if fund:
        old=screen(fund['snapshot'])
        marginal=[r['code'] for r in old['candidates'] if r['metrics']['roe']<12]
        if marginal:
            choices.append(candidate('fundamental', {'roe_min':12},
                '原版通过但 ROE<12 的 '+str(len(marginal))+' 个样本；检验收紧质量因子是否减少下行，不声称已改善收益',
                digest(fund),effective_at))
        else:reasons.append('fundamental_no_marginal_quality_evidence_keep')
    if tech:
        usable=[]
        for p in tech['bundle']['panels']:
            r,c=decide(p,tech['bundle']['asof'],None,False,read(POLICY))
            if r['status']=='ok':usable.append({'code':p['code'],'action':c['action'],'entry':r.get('entry')})
        if usable:
            choices.append(candidate('technical', {'breakout_volume_min':1.4},
                '原引擎可计算 '+str(len(usable))+' 个面板；检验更严格放量确认，风险退出完全不变；尚无收益证据',
                digest(tech),effective_at))
        else:reasons.append('technical_no_usable_panel_keep')
    return {'candidates':choices,'keep_reasons':reasons}


def fundamental_engines(e, choices):
    from .candidates import screen
    baseline=screen(e['snapshot']); versions={'qv-shadow-1':baseline}
    for c in choices:
        if c['category']=='fundamental':
            r=screen(e['snapshot'], parameters=c['parameters']);r['method_version']=c['id']
            versions[c['id']]=r
    return {'versions':versions,'source_hash':digest(e),'scope_codes':e['scope_codes'],
            'computed':True,'selected_counts':{k:len(v['candidates']) for k,v in versions.items()}}


def technical_engines(root, run_dir, e, scope_path, choices):
    from datetime import timedelta
    from .action_loop import observe, snapshots, register_policy, POLICY
    from .iteration_policy import action_policy
    from .timing import instant
    bundle=e['bundle']; outputs={}
    variants=[None]+[c for c in choices if c['category']=='technical']
    for c in variants:
        version=c['id'] if c else 'signal-policy-v1'
        ar=child(root,'technical-ledgers/'+version)
        # Register with original MT13 function, never bypass production promotion.
        if c and not (ar/'policy-registrations'/(version+'.json')).exists():
            seed=run_dir/('seed-'+version+'.json');atomic_json(seed,bundle)
            observe(seed,scope_path,ar,execution_model='observed-quote-v1')
            cfg=action_policy(c);policy=run_dir/(version+'.json');atomic_json(policy,cfg)
            register_policy(ar,policy)
        else:
            cfg=action_policy(c);policy=run_dir/(version+'.json');atomic_json(policy,cfg)
        # Only decision timestamp advances; provider fetched_at/bar times unchanged.
        b={**bundle,'asof':max(bundle['asof'],c['effective_at'],key=instant)} if c else bundle
        bp=run_dir/('execute-'+version+'.json');atomic_json(bp,b)
        receipt=observe(bp,scope_path,ar,policy if c else POLICY,execution_model='observed-quote-v1')
        state=snapshots(ar)[-1][1]
        outputs[version]={'receipt':receipt,'state':state,'state_hash':digest(state),
                          'ledger_root':str(ar),'policy':cfg}
    return {'versions':outputs,'computed':True,'source_hash':digest(e),
            'scope_codes':sorted(bundle['scope_codes']),'execution_model':'observed-quote-v1'}
