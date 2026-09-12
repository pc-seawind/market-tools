"""MT14 single run/status/verify/demo entrypoint, isolated durable stages."""
import fcntl
import json
import os
from contextlib import contextmanager
from pathlib import Path
from .data import atomic_json as _atomic_json
from .action_loop import now, read
from .timing import digest
from .timing_cli import file_hash
from .iteration_policy import SCHEMA, KINDS, CONTRACT

IMPLEMENTATION_GAPS = []


def atomic_json(path, value):
    _atomic_json(path, value)
    fd = os.open(str(Path(path).parent), os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)


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
    for c in choices:
        c['scope_codes']=list(fund['scope_codes'] if c['category']=='fundamental' else tech['bundle']['scope_codes'])
    return {'candidates':choices,'keep_reasons':reasons}


def select_generation(root, inputs):
    """Keep pending cohorts fixed; only mature terminal reviews permit a successor."""
    from .iteration_policy import candidate, BASE, BOUNDS
    registry=child(root,'candidates.json')
    old=read(registry) if registry.exists() else None
    reviews={}
    for p in sorted(child(root,'runs').glob('*/evaluation.json')):
        if not (p.parent/'manifest.json').exists():continue
        for r in read(p)['result']:reviews[r['candidate']]=r
    if old and old['candidates'] and any(reviews.get(c['id'],{}).get('decision') in ('reject','experimental_activate') for c in old['candidates']):
        choices=[];reasons=[]
        for c in old['candidates']:
            if reviews.get(c['id'],{}).get('decision') not in ('reject','experimental_activate'):
                choices.append(c);continue
            cat=c['category'];key='roe_min' if cat=='fundamental' else 'breakout_volume_min'
            value=round(c['parameters'][key]+(1 if cat=='fundamental' else .1),8)
            if value>BOUNDS[cat][key][1]:
                reasons.append(cat+'_whitelist_budget_exhausted_keep');continue
            active=child(root,'releases/'+cat+'/active.json')
            baseline=read(active)['version'] if active.exists() else ('qv-shadow-1' if cat=='fundamental' else 'signal-policy-v1')
            basefile=child(root,'candidate-descriptors/'+baseline+'.json')
            descriptor=read(basefile) if basefile.exists() else None
            n=candidate(cat,{**c['parameters'],key:value},
                '前代 '+c['id']+' 经成熟前向复算为 '+reviews[c['id']]['decision']+'；按预注册步长继续收紧，不回看择优',
                digest(reviews[c['id']]),inputs['asof'])
            n['scope_codes']=list(inputs.get('fundamental',{}).get('scope_codes',c.get('scope_codes',[])) if cat=='fundamental' else inputs.get('technical',{}).get('bundle',{}).get('scope_codes',c.get('scope_codes',[])))
            n.update(generation_from=c['id'],baseline_version=baseline,
                     baseline_parameters=descriptor['parameters'] if descriptor else BASE[cat],baseline_candidate=descriptor)
            choices.append(n)
        value={'candidates':choices,'keep_reasons':reasons}
    elif old is not None:value=old
    else:value=propose(inputs['fundamental'],inputs['technical'],inputs['asof'])
    value['active_baselines']={}
    for category in ('fundamental','technical'):
        pointer=child(root,'releases/'+category+'/active.json')
        if pointer.exists():
            desc=child(root,'candidate-descriptors/'+read(pointer)['version']+'.json')
            if desc.exists():value['active_baselines'][category]=read(desc)
    for c in value['candidates']:
        if c['category']=='technical' and not child(root,'technical-ledgers/'+c['id']+'/policy-registrations/'+c['id']+'.json').exists():
            c['effective_at']=inputs['asof']
        atomic_json(child(root,'candidate-descriptors/'+c['id']+'.json'),c)
    atomic_json(registry,value)
    return value


def fundamental_engines(e, choices, active=None):
    from .candidates import screen
    baseline=screen(e['snapshot']); versions={'qv-shadow-1':baseline}
    if active:
        r=screen(e['snapshot'],parameters=active['parameters']);r['method_version']=active['id'];versions[active['id']]=r
    for c in choices:
        if c['category']=='fundamental':
            if c.get('baseline_version') and c['baseline_version'] not in versions:
                base=screen(e['snapshot'], parameters=c['baseline_parameters']);base['method_version']=c['baseline_version']
                versions[c['baseline_version']]=base
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
    # Continue old virtual positions and explicit active baseline under THEIR policy.
    active_path=child(root,'releases/technical/active.json')
    active_version=read(active_path)['version'] if active_path.exists() else 'signal-policy-v1'
    tracked={c['id'] for c in variants if c}
    for p in child(root,'candidate-descriptors').glob('technical-*.json'):
        prior_candidate=read(p);version=prior_candidate['id'];ar=child(root,'technical-ledgers/'+version)
        history=snapshots(ar) if ar.exists() else []
        oldstate=history[-1][1] if history else {}
        needs_exit=bool(oldstate.get('positions')) or any(o['execution_status'] not in ('filled','cancelled') and not o.get('operator_paused') for o in oldstate.get('ledger',{}).values())
        if version not in tracked and (version==active_version or needs_exit):variants.append(prior_candidate);tracked.add(version)
    for c in choices:
        base=c.get('baseline_candidate')
        if c['category']=='technical' and base and base['id'] not in tracked:
            variants.append(base);tracked.add(base['id'])
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
        capture={'status':'no_pending_signal','not_deployed':True}
        if bundle.get('source_kind')!='SYNTHETIC_ONLY' and any(o['execution_status'] not in ('filled','cancelled') and not o.get('operator_paused') for o in state['ledger'].values()):
            from .action_worker import run_worker
            dest=run_dir/('execution-capture-'+version)
            capture=read(dest/'worker-result.json') if (dest/'worker-result.json').exists() else run_worker(scope_path,ar,dest,mode='once',max_seconds=30)
        outputs[version]={'receipt':receipt,'state':state,'state_hash':digest(state),
                          'ledger_root':str(ar),'policy':cfg,'execution_capture':capture}
    return {'versions':outputs,'computed':True,'source_hash':digest(e),
            'scope_codes':sorted(bundle['scope_codes']),'execution_model':'observed-quote-v1'}


def collect_inputs(root, run_dir, sweep, scope):
    from .iteration_evidence import fundamental, technical, collect_marks
    from .action_collect import collect
    from .timing import instant
    import shutil
    gaps=[]
    scope_copy=run_dir/'scope.json';scope_copy.parent.mkdir(parents=True,exist_ok=True)
    if not scope_copy.exists():shutil.copyfile(scope,scope_copy)
    elif file_hash(scope_copy)!=file_hash(scope):raise ValueError('scope_changed_mid_run')
    # Collector's own bundle is its completion receipt; never consume partial output.
    target=run_dir/'technical-collection'
    def technical_collect():
        completed=target/'supplement/bundle.json'
        if completed.exists():return str(completed)
        attempt=target if not target.exists() else run_dir/('technical-retry-'+digest(now())[:10])
        return collect(scope_copy,attempt)['bundle']
    bp=stage(root,run_dir,'technical-collection',technical_collect)
    tech=stage(root,run_dir,'technical-input',lambda:technical(bp,run_dir/'technical',now()))
    calendar=tech['bundle'].get('markets',{}).get('CN',{}).get('sessions',[])
    if not calendar:raise ValueError('CN_completed_calendar_missing_actual_collection_attempted')
    completed=calendar[-1]
    if sweep is None:
        sweep=Path(__file__).resolve().parents[1]/'.cron_state/mt1/sweeps'/completed
    if not (Path(sweep)/'summary.json').exists() or read(Path(sweep)/'summary.json')['asof']!=completed:
        from .sweep import sweep as collect_sweep
        # Bounded supplement into experiment-owned paths; no production sweep mutation.
        receipt=stage(root,run_dir,'sweep-remedy',lambda:collect_sweep(run_dir/'recollection',completed,
                      batch_size=100,workers=2,max_batches=1,max_seconds=180))
        sweep=run_dir/'recollection/sweeps'/completed
    fund=stage(root,run_dir,'fundamental-input',lambda:fundamental(sweep,run_dir/'fundamental',now()))
    marks=stage(root,run_dir,'supplement',lambda:collect_marks(run_dir/'supplement',fund['snapshot']['asof']))
    return {'fundamental':fund,'technical':tech,'marks':marks,'scope_path':str(scope_copy),
            'source_scope_hash':file_hash(scope),'asof':now(),'gaps':gaps}


def build_frames(inputs, selection, fundamentals, technicals, kind):
    from .iteration_evidence import mark_rows
    frames=[];gaps=[]
    tech=inputs['technical']['bundle']; calendar=tech.get('markets',{}).get('CN',{}).get('sessions',[])
    # Supplement retains markets for current real collection. Synthetic uses panels.
    if not calendar:
        calendar=next((p['sessions'] for p in tech['panels'] if p['market']=='CN'),[])
    previous=calendar[-2] if len(calendar)>1 else None
    for c in selection['candidates']:
        cat=c['category']; baseline=c.get('baseline_version', 'qv-shadow-1' if cat=='fundamental' else 'signal-policy-v1')
        if cat=='fundamental':
            rows,missing=mark_rows(inputs['marks'],inputs['asof']);gaps+=missing
            scopes=c.get('scope_codes',fundamentals['scope_codes']);v=fundamentals['versions']
            old={x['code'] for x in v[baseline]['candidates']};new={x['code'] for x in v[c['id']]['candidates']}
            rows=[{**r,'previous_session':previous,'baseline_selected':r['code'] in old,
                   'candidate_selected':r['code'] in new} for r in rows if r['code'] in scopes]
        else:
            scopes=c.get('scope_codes',technicals['scope_codes']);v=technicals['versions'];rows=[]
            old={x['code']:x for x in v[baseline]['state']['cards']};new={x['code']:x for x in v[c['id']]['state']['cards']}
            for p in tech['panels']:
                code=p['code']
                if old[code]['action']=='DATA_BLOCKED' or new[code]['action']=='DATA_BLOCKED':continue
                if not p.get('bars') or p.get('adjustment')!='vendor_factor_verified':continue
                b=p['bars'][-1]
                rows.append({'code':code,'price':b['close']*b['factor'],'market':p['market'],
                             'basis':p['basis_id'],'date':b['date'],'close_at':b['close_at'],
                             'previous_session':p['sessions'][-2] if len(p['sessions'])>1 else None,
                             'baseline_selected':old[code]['action']=='BUY','candidate_selected':new[code]['action']=='BUY'})
        frames.append({'kind':kind,'category':cat,'baseline':baseline,'candidate':c['id'],
                       'contract_hash':digest(CONTRACT),'observed_at':inputs['asof'],'scope_codes':sorted(scopes),
                       'capital_per_code':CONTRACT['capital_per_code'],'cost_bps':CONTRACT['per_side_cost_bps'],
                       'rows':rows,'sources_hash':digest(inputs)})
    return {'frames':frames,'gaps':gaps}


def run_files(run_dir):
    return {str(p.relative_to(run_dir)):file_hash(p) for p in sorted(run_dir.rglob('*'))
            if p.is_file() and p.name not in ('manifest.json','failure.json')}


def verify_run(root, run_dir):
    from .iteration_evidence import verify_fundamental, verify_technical
    from .action_loop import decide
    from .scope import load
    from .iteration_policy import code_hashes
    m=read(run_dir/'manifest.json')
    if m['files']!=run_files(run_dir):raise ValueError('run_manifest_hash_mismatch')
    if m['contract_hash']!=digest(CONTRACT) or m['code_hashes']!=code_hashes():raise ValueError('engine_or_contract_version_changed')
    def result(name):
        r=read(run_dir/(name+'.json'))
        if digest(r['result'])!=r['sha256']:raise ValueError('stage_hash_changed')
        return r['result']
    inputs=result('inputs');select=result('selection');fund=result('fundamental-engines');tech=result('technical-engines')
    verify_fundamental(inputs['fundamental'])
    verify_technical(inputs['technical'])
    if fundamental_engines(inputs['fundamental'],select['candidates'],select.get('active_baselines',{}).get('fundamental'))!=fund:
        raise ValueError('fundamental_engine_recompute_mismatch')
    from .action_loop import snapshots
    for version,value in tech['versions'].items():
        history=snapshots(value['ledger_root']);path=value['receipt']['manifest']
        idx=next(i for i,(p,s) in enumerate(history) if str(p)==str(path))
        state=history[idx][1]
        if digest(state)!=value['state_hash'] or state!=value['state']:raise ValueError('action_ledger_changed')
        # Re-run the actual pure action decision using its previous frozen state.
        prior=history[idx-1][1] if idx else {'states':{},'positions':{}}
        scope=load(inputs['scope_path']);bundle=read(run_dir/('execute-'+version+'.json'))
        for panel in bundle['panels']:
            key=version+'|'+panel['code'];held=panel['code'] in scope['holdings'] or key in prior['positions']
            _,card=decide(panel,bundle['asof'],prior['states'].get(key),held,value['policy'])
            actual=next(c for c in state['cards'] if c['code']==panel['code'])
            if card['action']!=actual['action']:
                raise ValueError('technical_action_recompute_mismatch:'+panel['code'])
    if build_frames(inputs,select,fund,tech,m['kind'])!=result('frames'):
        raise ValueError('forward_frame_recompute_mismatch')
    return m


def load_frames(root, verify=False):
    root=Path(root);frames=[]
    for p in sorted(child(root,'runs').glob('*/manifest.json'), key=lambda p:read(p.parent/'inputs.json')['result']['asof']):
        if verify:verify_run(root,p.parent)
        frames+=read(p.parent/'frames.json')['result']['frames']
    return frames


def status(root):
    root=Path(root).resolve();identity=read(child(root,'.mt14.json'))
    runs=[]
    for d in sorted(child(root,'runs').glob('*')):
        if (d/'failure.json').exists():
            failure=read(d/'failure.json')
            if (d/'recovery.json').exists():
                target=Path(read(d/'recovery.json')['superseded_by'])
                failure={**failure,'engineering_status':'recovered_attempt' if (target/'manifest.json').exists() else 'incomplete','recovery':read(d/'recovery.json')}
            runs.append(failure)
        elif (d/'manifest.json').exists():runs.append(read(d/'manifest.json')['summary'])
        else:runs.append({'run_id':d.name,'engineering_status':'in_progress'})
    return {'identity':identity,'scheduled':False,'runs':runs,
            'active':{p.parent.name:read(p) for p in child(root,'releases').glob('*/active.json')}}


def verify(root):
    root=Path(root).resolve();verified=[]
    for p in sorted(child(root,'runs').glob('*/manifest.json'), key=lambda p:read(p.parent/'inputs.json')['result']['asof']):
        verified.append({'run':p.parent.name,'hash':file_hash(p),'summary':verify_run(root,p.parent)['summary']})
    for p in child(root,'events').glob('*.json'):
        r=read(p)
        if digest(r['value'])!=r['sha256']:raise ValueError('event_tampered')
    # Independently recalculate archived decisions from the corresponding prefix.
    from .iteration_validate import validate
    frames=[]
    for p in sorted(child(root,'runs').glob('*/manifest.json'), key=lambda p:read(p.parent/'inputs.json')['result']['asof']):
        frames+=read(p.parent/'frames.json')['result']['frames'];stored=read(p.parent/'evaluation.json')['result']
        for expected in stored:
            cat=expected['category'];old=expected['baseline']
            actual=validate(frames,cat,old,expected['candidate'],kind=expected['kind'],asof=expected['asof'])
            if actual!=expected:raise ValueError('evaluation_recompute_mismatch')
    return {'verified_runs':verified,'scheduled':False,'engineering_verification_only':True,
            'incomplete':[r for r in status(root)['runs'] if r.get('engineering_status') not in ('completed','recovered_attempt')]}


def run(root, *, sweep, scope, request_id=None, fail_at=None):
    from datetime import timedelta
    from .iteration_policy import code_hashes
    from .iteration_validate import validate
    from .iteration_release import recover, publish
    root=init(root,'REAL_CURRENT');key=request_id or now()[:10]
    # User key never becomes an unsafe path; date prefix gives chronological order.
    rid=key[:10].replace('/','_')+'-'+digest(key)[:16]
    d=child(root,'runs/'+rid)
    with locked(root):
        request_path=child(root,'requests/'+digest(key)+'.json')
        if request_path.exists():
            request=read(request_path)
            if request['scope_hash']!=file_hash(scope):raise ValueError('request_scope_changed')
            d=child(root,request['run_path']);rid=d.name
        recover(root)
        # A failed or expired attempt is retained, not overwritten or backdated.
        # Same user command automatically starts a fresh current-time attempt.
        stale=False
        if (d/'inputs.json').exists() and not (d/'technical-engines.json').exists():
            source_time=read(d/'inputs.json')['result']['technical']['bundle']['asof']
            stale=(instant_time(now())-instant_time(source_time)).total_seconds()>1800
        if (d/'failure.json').exists() or (not (d/'manifest.json').exists() and stale):
            prior=d
            d=child(root,'runs/'+rid.split('-retry-')[0]+'-retry-'+now().replace(':','').replace('.',''))
            rid=d.name
            atomic_json(prior/'recovery.json',{'superseded_by':str(d),'reason':'fresh_collection_retry','at':now()})
        atomic_json(request_path,{'scope_hash':file_hash(scope),'run_path':str(d.relative_to(root))})
        if (d/'manifest.json').exists():
            verify_run(root,d)
            return {**read(d/'manifest.json')['summary'],'idempotent':True}
        d.mkdir(parents=True,exist_ok=True)
        try:
            inputs=stage(root,d,'inputs',lambda:collect_inputs(root,d,sweep,scope))
            select=stage(root,d,'selection',lambda:select_generation(root,inputs))
            f=stage(root,d,'fundamental-engines',lambda:fundamental_engines(inputs['fundamental'],select['candidates'],select.get('active_baselines',{}).get('fundamental')))
            t=stage(root,d,'technical-engines',lambda:technical_engines(root,d,inputs['technical'],inputs['scope_path'],select['candidates']))
            if fail_at=='after_engines':raise RuntimeError('injected_after_engines')
            current=stage(root,d,'frames',lambda:build_frames(inputs,select,f,t,'REAL_CURRENT'))
            frames=load_frames(root,verify=True)+current['frames']
            def evaluate():
                return [validate(frames,c['category'],c.get('baseline_version','qv-shadow-1' if c['category']=='fundamental' else 'signal-policy-v1'),
                                 c['id'],kind='REAL_CURRENT',asof=inputs['asof']) for c in select['candidates']]
            evaluations=stage(root,d,'evaluation',evaluate)
            gaps=current['gaps']+inputs['technical']['missing']+IMPLEMENTATION_GAPS
            summary={'run_id':rid,'engineering_status':'incomplete' if gaps else 'completed',
                     'round_execution_status':'completed','implementation_status':'incomplete' if IMPLEMENTATION_GAPS else 'completed',
                     'strategy_decisions':[{k:r[k] for k in ('category','candidate','decision','reason','independent_events')} for r in evaluations],
                     'candidate_count':len(select['candidates']),'fundamental_selected_counts':f['selected_counts'],
                     'fundamental_scope_count':len(f['scope_codes']),'technical_scope_count':len(t['scope_codes']),
                     'technical_actions':{v:{c['code']:c['action'] for c in x['state']['cards']} for v,x in t['versions'].items()},
                     'kind':'REAL_CURRENT','gaps':gaps,'scheduled':False,
                     'next_check_at':(instant_time(inputs['asof'])+timedelta(days=1)).isoformat(),
                     'production_activated':False,'no_efficacy_claim':True}
            atomic_json(d/'summary.json',summary)
            (d/'report.md').write_text('**MT14 真实首轮/续轮｜非投资验收**\n\n'+json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
            if fail_at=='before_archive':raise OSError('injected_archive_failure')
            manifest={'schema':SCHEMA,'kind':'REAL_CURRENT','contract_hash':digest(CONTRACT),
                      'code_hashes':code_hashes(),'files':run_files(d),'summary':summary}
            atomic_json(d/'manifest.json',manifest)
            verify_run(root,d)
            event(root,'run:'+rid,{'manifest_hash':file_hash(d/'manifest.json'),'engineering_status':summary['engineering_status']})
            # Release only verified completed evidence; no candidate pass field.
            ep=child(root,'evaluations/'+digest(frames)+'.json');atomic_json(ep,frames)
            if not gaps:
                for c in select['candidates']:
                    publish(root,ep,c['category'],c.get('baseline_version','qv-shadow-1' if c['category']=='fundamental' else 'signal-policy-v1'),c['id'],inputs['asof'])
            return summary
        except Exception as exc:
            receipt={'run_id':rid,'engineering_status':'incomplete','error':type(exc).__name__+':'+str(exc),
                     'scheduled':False,'resume_command':'same run command','recorded_at':now()}
            try:atomic_json(d/'failure.json',receipt)
            except OSError:
                # Last-resort local receipt outside failed run archive; CLI also prints error.
                atomic_json(child(root,'last-failure.json'),receipt)
            raise


def instant_time(value):
    from .timing import instant
    return instant(value)


def demo(root):
    """Actual synthetic engine runs + pointer mutations; never counted as real."""
    from datetime import date, timedelta
    from .iteration_release import publish, recover
    from .iteration_policy import candidate
    from .candidates import screen
    from .action_demo import demo as action_demo, fixture, bundle, make_scope, append
    from .action_loop import snapshots
    from .iteration_validate import validate
    root=init(root,'SYNTHETIC_ONLY')
    with locked(root):
        if (root/'demo-summary.json').exists():
            expected=read(root/'demo-summary.json')
            for p,h in expected['artifacts'].items():
                if file_hash(p)!=h:raise ValueError('demo_artifact_tampered')
            from .candidates import screen
            # Hash checks are necessary, not sufficient: reconstruct generated engine outputs.
            for source in (root/'raw').glob('*.json'):
                record=read(source)
                if screen(record['snapshot'])!=record['baseline'] or screen(record['snapshot'],{'roe_min':12})!=record['candidate']:
                    raise ValueError('synthetic_engine_recompute_mismatch')
            for filename,field in [('forward.json','activation'),('reject-forward.json','rejection')]:
                saved=expected[field];frames=read(root/filename)
                recomputed=validate(frames,'fundamental','qv-shadow-1',saved['candidate'],kind='SYNTHETIC_ONLY',asof=saved['asof'])
                if any(saved.get(k)!=v for k,v in recomputed.items()):raise ValueError('synthetic_evaluation_recompute_mismatch')
                for f in frames:
                    source=read(root/'raw'/(f['rows'][0]['date']+'.json'))
                    price=source['declining_price' if field=='activation' else 'rising_price']
                    old={c['code'] for c in source['baseline']['candidates']};new={c['code'] for c in source['candidate']['candidates']}
                    if any(r['price']!=price or r['baseline_selected']!=(r['code'] in old) or r['candidate_selected']!=(r['code'] in new) for r in f['rows']):
                        raise ValueError('synthetic_frame_source_mismatch')
            return {**expected,'idempotent':True}
        raw=child(root,'raw');raw.mkdir(exist_ok=True)
        frames=[];reject_frames=[];start=date(2026,9,14);dates=[]
        while len(dates)<62:
            if start.weekday()<5:dates.append(str(start))
            start+=timedelta(days=1)
        c=candidate('fundamental',{'roe_min':12},'SYNTHETIC ROE boundary','SYNTHETIC',dates[0]+'T18:00:00+00:00')
        codes=['SYNTH-'+str(n) for n in range(20)]
        for i,day in enumerate(dates):
            snap={'asof':day,'data_version':'SYNTHETIC','universe_size':len(codes),
                  'observations':[{'stock':{'ts_code':code,'name':'SYNTHETIC_ONLY'},
                                   'daily':{'trade_date':day.replace('-',''),'pe_ttm':15,'pb':2,'turnover_rate':1},
                                   'financials':[{'ann_date':'20260801','end_date':'20260630','roe':11,'ocfps':1,'eps':1,'debt_to_assets':30}]} for code in codes]}
            old=screen(snap);new=screen(snap,c['parameters'])
            selected_old={x['code'] for x in old['candidates']};selected_new={x['code'] for x in new['candidates']}
            atomic_json(raw/(day+'.json'),{'SYNTHETIC_ONLY':True,'snapshot':snap,'baseline':old,'candidate':new,
                                           'declining_price':100-i*.5,'rising_price':100+i*.5})
            f={'kind':'SYNTHETIC_ONLY','category':'fundamental','baseline':'qv-shadow-1','candidate':c['id'],
               'contract_hash':digest(CONTRACT),'observed_at':day+'T18:00:00+00:00','scope_codes':codes,
               'capital_per_code':CONTRACT['capital_per_code'],'cost_bps':CONTRACT['per_side_cost_bps'],
               'rows':[{'code':code,'date':day,'close_at':day+'T15:00:00+00:00','market':'CN','basis':'SYNTHETIC',
                        'previous_session':dates[i-1] if i else None,'price':100-i*.5,
                        'baseline_selected':code in selected_old,'candidate_selected':code in selected_new} for code in codes]}
            frames.append(f);reject_frames.append({**f,'rows':[{**r,'price':100+i*.5} for r in f['rows']]})
        ep=child(root,'forward.json');atomic_json(ep,frames);asof=frames[-1]['observed_at']
        args=(root,ep,'fundamental','qv-shadow-1',c['id'],asof)
        try:publish(*args,fail_at='after_prepare')
        except RuntimeError as exc:event(root,'demo-interruption',{'error':str(exc)})
        recovered=recover(root)
        activated=publish(*args)
        active_before=read(root/'releases/fundamental/active.json')
        # Tamper actual bound evidence; health check must restore baseline pointer.
        original=ep.read_bytes();ep.write_text('[]')
        rolled=recover(root);active_after=read(root/'releases/fundamental/active.json')
        ep.write_bytes(original)
        reject_path=root/'reject-forward.json';atomic_json(reject_path,reject_frames)
        rejected=publish(root,reject_path,'fundamental','qv-shadow-1',c['id'],asof)
        unfinished=validate(frames[:10],'fundamental','qv-shadow-1',c['id'],kind='SYNTHETIC_ONLY',asof=asof)
        assert recovered[0]['action']=='resume_prepared'
        assert activated['decision']=='experimental_activate' and activated['idempotent']
        assert active_before['version']==c['id'] and active_after['version']=='qv-shadow-1'
        assert rolled[0]['action']=='automatic_rollback' and rejected['decision']=='reject'
        assert unfinished['decision']=='continue_shadow'
        # Original execution engine performs actual SYNTHETIC buy/exit/fills.
        trade=action_demo(root/'execution-roundtrip')
        # Paired original/candidate technical engine calculation, including registration.
        scope=root/'synthetic-scope.json';make_scope(scope)
        p=fixture();c2=candidate('technical',{'breakout_volume_min':1.4},'SYNTHETIC volume boundary','SYNTHETIC',p['fetched_at'][:-6]+'.000001+08:00')
        paired=[]
        for i in range(3):
            if i:p=append(p,108 if i==1 else 109,130 if i==1 else 160)
            d=root/('paired-'+str(i));d.mkdir()
            bp=bundle(p,d,i)
            e={'bundle':read(bp)}
            paired.append(technical_engines(root,d,e,scope,[c2]))
        for i,x in enumerate(paired):atomic_json(root/('paired-output-'+str(i)+'.json'),x)
        result={'kind':'SYNTHETIC_ONLY','scheduled':False,'engineering_status':'completed',
                'activation':activated,'rejection':rejected,'immature':unfinished,'recovery':recovered,
                'tamper_rollback':rolled,'pointer_before':active_before,'pointer_after':active_after,
                'execution_roundtrip':trade,'paired_technical_versions':list(paired[-1]['versions']),
                'real_sample_count':0,'no_efficacy_claim':True}
        (root/'demo-report.md').write_text('**SYNTHETIC_ONLY｜不是行情/收益证据**\n'+json.dumps(result,ensure_ascii=False,indent=2))
        result['artifacts']={str(p):file_hash(p) for p in root.rglob('*') if p.is_file() and p.name not in ('.iteration.lock','demo-summary.json')}
        atomic_json(root/'demo-summary.json',result)
        return result


def main(argv=None):
    import argparse
    p=argparse.ArgumentParser(description='MT14 isolated iteration; no production/cron/trades')
    p.add_argument('command',choices=['run','status','verify','demo'])
    p.add_argument('--root',required=True)
    p.add_argument('--sweep', help='可选指定 sweep；默认自动选择最新快照，过期实际补采')
    p.add_argument('--scope',default='/home/emox/work/investment/reference/tracking-scope.json')
    p.add_argument('--request-id')
    a=p.parse_args(argv)
    try:
        if a.command=='run':r=run(a.root,sweep=a.sweep,scope=a.scope,request_id=a.request_id)
        elif a.command=='demo':r=demo(a.root)
        elif a.command=='verify' and (Path(a.root)/'demo-summary.json').exists():r=demo(a.root)
        else:r=globals()[a.command](a.root)
        print(json.dumps(r,ensure_ascii=False,indent=2))
        return 2 if r.get('engineering_status')=='incomplete' or r.get('incomplete') else 0
    except Exception as e:
        print(json.dumps({'engineering_status':'incomplete','error':type(e).__name__+':'+str(e),'scheduled':False},ensure_ascii=False))
        return 2


if __name__=='__main__':
    raise SystemExit(main())
