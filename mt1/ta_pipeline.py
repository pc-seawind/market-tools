"""Existing evening report -> nonblocking bounded research revision + due queue.
No timer/cron/group changes. Each caller source/scope/code digest is a checkpoint.
A repeat is a read, a new daily source is a NEW freeze, never reuse yesterday input.
"""
import argparse
import fcntl
import json
import os
import subprocess
import sys
from pathlib import Path
from .ta_research import HERE, DEFAULT_ROOT, SCOPE, read, save, digest, now, run
from .timing_cli import file_hash
from .data import atomic_json
from .timing import instant

CATALOG=HERE/'reports/ta10-r2-20260915/company-sources'
DAILY=Path('/home/emox/work/investment/reference/daily-reports')


def identity(report,scope=SCOPE,catalog=CATALOG):
    report=Path(report)
    sources={str(p.relative_to(report)):file_hash(p) for p in sorted(report.rglob('*')) if p.is_file() and (p.parent==report or p.suffix in ('.json','.yaml'))}
    code={p.name:file_hash(p) for p in (HERE/'mt1').glob('ta_*.py')}
    documents={p.name:file_hash(p) for p in sorted(Path(catalog).glob('*.meta.json'))}
    return {'report':str(report.resolve()),'source_hashes':sources,'scope_hash':file_hash(scope),'code_hashes':code,'catalog_hashes':documents}


MAX_ATTEMPTS=3
BACKOFF_SECONDS=(60,300,900)


def unit_state(unit):
    """Unknown supervision is NOT permission to launch another billable worker."""
    try:
        cp=subprocess.run(['systemctl','--user','show',unit,'--property=ActiveState','--value'],capture_output=True,text=True,timeout=5)
        state=cp.stdout.strip()
        return state if state in ('active','activating','deactivating','inactive','failed') else 'unknown'
    except Exception:return 'unknown'


def request_refresh(report,phase,root=DEFAULT_ROOT,scope=SCOPE,catalog=CATALOG,recover_reason=None):
    from datetime import timedelta
    report=Path(report).resolve();root=Path(root)
    receipt={'not_published':True,'base_report_unblocked':True,'production_schedule_unchanged':True}
    if phase!='evening':return {**receipt,'status':'skipped_non_evening','continuation':'next_existing_evening_report'}
    if DAILY not in report.parents or not (report/'sources.json').is_file():return {**receipt,'status':'skipped_non_production_source'}
    try:
        ident=identity(report,scope,catalog);key=digest(ident)[:24];request=root/'requests'/key
        request.mkdir(parents=True,exist_ok=True)
        with (request/'.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            status=request/'launch.json';old=read(status) if status.exists() else {};current=read(request/'status.json') if (request/'status.json').exists() else {}
            attempts=old.get('attempt',1 if old else 0);limit=old.get('attempt_limit',MAX_ATTEMPTS)
            reply={**receipt,**old,'reused_request':True,'current':current,'request_id':key}
            if current.get('status','').startswith('completed'):
                return {**reply,'status':'completed','recovery':'not_needed'}
            if old.get('status') in ('launched','launch_unknown') or current.get('status')=='running':
                age=(instant(now())-instant(old.get('launched_at',current.get('started_at',now())))).total_seconds()
                # Startup race: worker might not yet have reached status.json.
                if age<30:return reply
                state=unit_state(old['unit'])
                if state in ('active','activating','deactivating','unknown'):
                    return {**reply,'supervision':state,'recovery':'wait_for_confirmed_exit'}
            if old:
                if old.get('next_retry_at') and instant(now())<instant(old['next_retry_at']):return {**reply,'recovery':'backoff'}
                if attempts>=limit:
                    if not recover_reason:return {**reply,'recovery':'attempt_limit','owner':'investment-agent','recovery_entry':'ta_workflow recover --request ID --reason REASON'}
                    limit=attempts+MAX_ATTEMPTS
                save(request/'history'/('launch-'+str(attempts)+'.json'),{'launch':old,'worker':current})
            attempt=attempts+1;unit='ta10-refresh-'+key+'-a'+str(attempt)
            if not (request/'input.json').exists():save(request/'input.json',{'key':key,'identity':ident,'scope':str(scope),'catalog':str(catalog),'created_at':now()})
            launched_at=now();next_at=(instant(launched_at)+timedelta(seconds=BACKOFF_SECONDS[min(attempt-1,2)])).isoformat()
            launch={**receipt,'request_id':key,'unit':unit,'status':'launch_unknown','attempt':attempt,'attempt_limit':limit,'launched_at':launched_at,'next_retry_at':next_at,'recovery_reason':recover_reason,'request_path':str(request.resolve())}
            # Persist intent BEFORE spawn. Timeout may mean systemd accepted it.
            atomic_json(status,launch);atomic_json(request/'status.json',{'status':'launching','attempt':attempt,'started_at':now()})
            cmd=['systemd-run','--user','--unit='+unit,'--property=RuntimeMaxSec=3600','--working-directory='+str(HERE)]
            cmd += ['--setenv='+name for name in ('HUOSHAN_API_KEY','ANTHROPIC_HUOSHAN_URL','TUSHARE_TOKEN','HTTP_PROXY','HTTPS_PROXY','NO_PROXY') if name in os.environ]
            cmd += ['/usr/bin/python3','-m','mt1.ta_pipeline','--root',str(root.resolve()),'--request',key]
            try:
                cp=subprocess.run(cmd,capture_output=True,text=True,timeout=8)
                launch.update(status='launched' if cp.returncode==0 else 'launch_failed',returncode=cp.returncode)
            except Exception as e:
                launch.update(error_type=type(e).__name__)
                atomic_json(status,launch)
                return {**launch,'status':'refresh_failed','recovery':'supervise_uncertain_launch_before_retry'}
            atomic_json(status,launch);return launch
    except Exception as e:return {**receipt,'status':'refresh_failed','error_type':type(e).__name__}


def due_queue(root,asof):
    """Read every immutable revision, record next-session evidence separately.
    Missing forward panels/new corporate proofs are explicit due blockers; no
    calendar-count fiction, price attribution, or silent business sign-off.
    """
    root=Path(root);pointers=list(root.glob('revisions/*/latest.json'))
    # An adopted, fully audited first run participates in future follow-ups too.
    if (root/'seed.json').exists():pointers.append(root/'seed.json')
    inputs=[]
    for p in pointers:
        ptr=read(p);mp=Path(ptr['manifest'])
        from .ta_research import verify_manifest
        if file_hash(mp)!=ptr['sha256']:raise ValueError('due_manifest_hash_mismatch')
        verify_manifest(mp);inputs.append((ptr,read(mp.parent/'input.json')))
    rows=[]
    from .ta_review import evaluate
    for ptr,frozen in inputs:
        for stock in frozen['stocks']:
            target=stock.get('target_session');available=[]
            for newer,nf in inputs:
                if instant(nf['as_of'])<=instant(frozen['as_of']):continue
                ns=next((s for s in nf['stocks'] if s['code']==stock['code']),None)
                if ns:
                    available += [e for e in ns['evidence'] if e.get('published_at') and instant(frozen['as_of'])<instant(e['published_at'])<=instant(asof)]
            from .ta_workflow import decisions, OWNER
            decision=decisions(root,ptr['sha256'],stock['code'],asof)
            if decision:available+=decision.get('post_evidence',[])
            available=list({e['evidence_id']:e for e in available}.values())
            # End-of-target-session is conservative (HK 16:10, CN 15:10).
            close=target+('T16:10:00+08:00' if stock['market']=='HK' else 'T15:10:00+08:00') if target else None
            quotes=[e for e in available if e['kind']=='quote' and target and instant(e['published_at']).astimezone(instant(close).tzinfo).date().isoformat()==target]
            short={'target_session':target,'status':'not_matured' if close and instant(asof)<instant(close) else 'blocked_missing_target_quote','evidence_ids':[e['evidence_id'] for e in quotes]}
            if quotes:short['status']='observed_pending_scenario_review'
            from .ta_forward import panel
            forward=panel(inputs,stock,frozen['as_of'],asof,root)
            evaluation=evaluate(frozen['as_of'],asof,forward['sessions'],forward['observations'],available+forward['evidence'],decision['proposition_checks'] if decision else [],forward['calendar'])
            evaluation['calendar_status']=forward['status']
            evaluation['missing_quote_sessions']=forward.get('missing_quote_sessions',[])
            if forward['status']!='verified_calendar':
                for h in evaluation['horizons']:h['status']='blocked_calendar_gap'
            if decision:short.update(analyst_judgment=decision['short_term'],reviewed_at=decision['reviewed_at'])
            rows.append({'owner':OWNER,'next_check_at':decision['next_check_at'] if decision else asof,'review_writeback':decision,'run_id':ptr['run_id'],'code':stock['code'],'original_manifest_hash':ptr['sha256'],'short_term':short,'evaluation':evaluation,'new_evidence_ids':[e['evidence_id'] for e in available],
                         'continuation':'existing_evening_report -> ta_pipeline.worker -> due_queue; exact panels/checks via ta_review.evaluate',
                         'business_status':evaluation['business_status'] if decision else ('due_new_company_evidence_review' if any(e['kind']=='company_primary' for e in available) else 'pending_new_company_evidence'),
                         'error_categories':decision['error_categories'] if decision else {k:'unknown' for k in ('data','logic','timing','pricing','unexpected_event')}})
    out={'as_of':asof,'items':rows,'count':len(rows),'no_personal_pnl':True,'not_published':True}
    path=root/'followups'/(digest(out)+'.json');save(path,out);atomic_json(root/'due-latest.json',{'path':str(path.resolve()),'sha256':file_hash(path),'count':len(rows)})
    return {'path':str(path),'sha256':file_hash(path),'count':len(rows)}


def _worker(root,key):
    root=Path(root);request=root/'requests'/key;task=read(request/'input.json');status=request/'status.json'
    try:
        if digest(identity(task['identity']['report'],task['scope'],task['catalog']))[:24]!=key:raise ValueError('queued_inputs_changed_new_revision_required')
        atomic_json(status,{'status':'running','started_at':now()})
        result=run(root/'revisions'/key,task['identity']['report'],task['scope'],task['catalog'])
        from .ta_quality import automatic
        mp=Path(result['manifest']);quality=automatic(mp)
        # Recomputed QA is versioned outside immutable inference files.
        quality_path=root/'quality-history'/(digest(quality)+'.json');save(quality_path,quality)
        # This pointer exposes coverage, NOT directions until analyst review exists.
        pointer={'manifest':str(mp.resolve()),'sha256':file_hash(mp),'run_id':result['run_id'],'quality_status':'review_required','automatic_quality_path':str(quality_path.resolve()),'automatic_quality_hash':file_hash(quality_path)}
        atomic_json(root/'latest.json',pointer)
        due=due_queue(root,now());done={'status':'completed_pending_analyst_review',**result,'due_queue':due,'finished_at':now(),'not_published':True};atomic_json(status,done);return done
    except Exception as e:
        failed={'status':'failed','error_type':type(e).__name__,'reason':str(e),'finished_at':now(),'base_report_unblocked':True};atomic_json(status,failed);return failed

def worker(root,key):
    request=Path(root)/'requests'/key
    with (request/'worker.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return {'status':'already_running','not_published':True}
        prior=read(request/'status.json') if (request/'status.json').exists() else {}
        if prior.get('status','').startswith('completed'):return prior
        return _worker(root,key)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',default=str(DEFAULT_ROOT));p.add_argument('--request',required=True);a=p.parse_args();print(json.dumps(worker(a.root,a.request),ensure_ascii=False))
