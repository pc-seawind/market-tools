"""Append-only, hash-linked run/review/delivery evidence; not a publisher.

A receipt is a recorded provider response + readback, not a proof manufactured by
an exit code or by a local unit test. Missing scheduler evidence stays pending.
"""
import hashlib
import json
import os
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from .data import atomic_json
from .store import digest


def file_ref(path):
    p=Path(path).resolve()
    return {'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}


def chain_dir(state_dir,run_id):
    if not run_id or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in run_id):
        raise ValueError('invalid source_run_id')
    path=Path(state_dir)/'chains'/run_id;path.mkdir(parents=True,exist_ok=True);return path


def append(state_dir,run_id,stage,data):
    root=chain_dir(state_dir,run_id)
    value={'run_id':run_id,'stage':stage,'recorded_at':datetime.now(timezone.utc).isoformat(),**data}
    p=root/(stage+'-'+digest(value)[:24]+'.json')
    with p.open('x') as f:json.dump(value,f,ensure_ascii=False,indent=2)
    return str(p)


def capture_run(state_dir,result,invocation_kind='unattributed'):
    from .data import HERE
    root=Path(state_dir)/'runs'/result['run_id']
    sha=subprocess.check_output(['git','-C',str(HERE),'rev-parse','HEAD'],text=True,timeout=5).strip()
    return append(state_dir,result['run_id'],'run',{'phase':result['phase'],
        'invocation_kind':invocation_kind,'scheduler_status':'not_linked',
        'hostname':socket.gethostname(),'worker_id':os.environ.get('HOMESPACE_WORKER_ID','unknown'),
        'pid':os.getpid(),'git_head':sha,'cwd':str(HERE),
        'source_files':[file_ref(p) for p in sorted((HERE/'mt1').glob('*.py'))]+[file_ref(HERE/'mt1.py'),file_ref(HERE/'mt1_job.py')],
        'manifest':file_ref(root/'manifest.json'),'report':file_ref(root/'report.json'),
        'manifest_snapshot':json.loads((root/'manifest.json').read_text()),
        'errors':result['errors']})


def validate_source(state_dir,bundle):
    rid=bundle.get('source_run_id')
    if not rid:return None
    chain_dir(state_dir,rid)
    p=Path(state_dir)/'runs'/rid/'report.json'
    report=json.loads(p.read_text())
    if report['phase']!=bundle['phase']:raise ValueError('review phase != source run phase')
    return report


def capture_review(state_dir,bundle,result):
    rid=bundle.get('source_run_id')
    if not rid:return None
    return append(state_dir,rid,'review',{'phase':bundle['phase'],'bundle':bundle,
        'bundle_hash':digest(bundle),'result':file_ref(result['result_json']),
        'markdown':file_ref(result['report_path']),'errors':result['errors'],
        'pending_review_ids':result.get('pending_review_ids',[]),
        'reviewed_plan_ids':result['reviewed_plan_ids']})


def attach(state_dir,run_id,kind,input_path):
    if kind not in ('dispatch','delivery'):raise ValueError('invalid evidence kind')
    root=chain_dir(state_dir,run_id);value=json.loads(Path(input_path).read_text())
    if value.get('run_id')!=run_id:raise ValueError('receipt run_id mismatch')
    if kind=='dispatch':
        for field in ('cron_name','scheduled_at','worker_id','topic_id','raw_log_path'):
            if not value.get(field):raise ValueError('missing dispatch '+field)
        raw=file_ref(value['raw_log_path'])
        return append(state_dir,run_id,kind,{'receipt':value,'raw':raw,
            'verification':'operator_linked_log_not_automatic_scheduler_attestation'})
    reviews=list(root.glob('review-*.json'))
    hashes={json.loads(p.read_text())['markdown']['sha256'] for p in reviews}
    if value.get('report_sha256') not in hashes:raise ValueError('delivery report hash not in run reviews')
    if not value.get('document_url') and not value.get('message_id'):raise ValueError('missing delivery identifier')
    raw=file_ref(value['provider_response_path']);readback=file_ref(value['readback_path'])
    if run_id not in Path(readback['path']).read_text():raise ValueError('readback lacks run_id marker')
    return append(state_dir,run_id,kind,{'receipt':value,'provider_response':raw,'readback':readback,
        'verification':'provider_response_and_content_readback_recorded; independent audit required'})


def inventory(state_dir):
    rows=[]
    for root in sorted((Path(state_dir)/'chains').glob('*')):
        if not root.is_dir():continue
        stages={k:[str(p) for p in sorted(root.glob(k+'-*.json'))] for k in ('dispatch','run','review','delivery')}
        integrity=[]
        for paths in stages.values():
            for p in paths:
                record=json.loads(Path(p).read_text())
                for key in ('manifest','report','markdown','result','raw','provider_response','readback'):
                    ref=record.get(key)
                    if isinstance(ref,dict) and ref.get('path'):
                        try:
                            if file_ref(ref['path'])['sha256']!=ref['sha256']:integrity.append({'record':p,'field':key,'error':'hash_changed'})
                        except (OSError,KeyError):integrity.append({'record':p,'field':key,'error':'missing_artifact'})
        rows.append({'run_id':root.name,'stages':stages,'integrity_errors':integrity,
            'missing':[k for k,v in stages.items() if not v],
            'status':'evidence_recorded_not_accepted' if all(stages.values()) and not integrity else 'pending_evidence'})
    phases={}
    for phase in ('morning','evening','saturday','sunday'):
        linked=[r for r in rows if r['run_id'].endswith('-'+phase)]
        phases[phase]={'natural_run_ids':[r['run_id'] for r in linked],
            'status':'evidence_recorded_for_review' if any(not r['missing'] and not r['integrity_errors'] for r in linked) else 'awaiting_natural_chain'}
    return {'chains':rows,'phases':phases,'acceptance':'independent_review_required'}


def collect_dispatch(state_dir,run_id):
    """Correlate gateway firing logs, not an assertion that a local test scheduled.

    Only the natural date-phase run id is eligible. Record correlation limits:
    binding snapshot is current, and scheduler log lacks a unique dispatch id.
    """
    import shlex
    from datetime import timedelta
    from zoneinfo import ZoneInfo
    names={'morning':'morning-market-brief','evening':'evening-market-recap',
           'saturday':'weekend-saturday-recap','sunday':'weekend-sunday-preview'}
    root=chain_dir(state_dir,run_id)
    report=json.loads((Path(state_dir)/'runs'/run_id/'report.json').read_text())
    if run_id!=report['asof']+'-'+report['phase']:
        return {'status':'manual_run_not_natural_schedule','dispatch_linked':False}
    start=datetime.fromisoformat(report['asof']).replace(tzinfo=ZoneInfo('Asia/Shanghai'))
    since=int(start.timestamp());until=int((start+timedelta(days=1)).timestamp());name=names[report['phase']]
    remote=f'''
import pathlib,json,subprocess
root=pathlib.Path.home()/'.homespace'
name={name!r}
bindings=json.loads((root/'cron-managed-threads.json').read_text())
found=[]
for slot,v in bindings.items():
 b=v.get('job_topics',{{}}).get(name)
 if b:found.append({{'slot':slot,**{{k:b.get(k) for k in ['worker_id','topic_id','bound_at']}}}})
cfg=json.loads((root/'cron'/(name+'.json')).read_text())
cp=subprocess.run(['journalctl','--user','-u','homespace-gateway','--since','@{since}','--until','@{until}','--no-pager','-o','json'],capture_output=True,text=True,timeout=15)
logs=[]
for line in cp.stdout.splitlines():
 try:
  x=json.loads(line)
  if 'cron: firing '+name+' ' in x.get('MESSAGE',''):logs.append({{'timestamp':x['__REALTIME_TIMESTAMP'],'message':x['MESSAGE']}})
 except (ValueError,KeyError):pass
print(json.dumps({{'bindings':found,'config':{{k:cfg.get(k) for k in ['name','cron','domain','cwd','delivery','silent','model']}},'logs':logs,'journal_exit':cp.returncode}}))
'''
    cp=subprocess.run(['ssh','-o','ConnectTimeout=8','vps','python3 -c '+shlex.quote(remote)],
        capture_output=True,text=True,timeout=25)
    if cp.returncode:return {'status':'gateway_evidence_unavailable','error':cp.stderr[-500:]}
    result=json.loads(cp.stdout);logs=result['logs'];bindings=result['bindings']
    if not logs or len(bindings)!=1:return {'status':'awaiting_unambiguous_dispatch','evidence':result}
    records=[json.loads(p.read_text()) for p in root.glob('run-*.json')]
    timestamps=[datetime.fromisoformat(v['recorded_at']).timestamp() for v in records
        if v['invocation_kind']=='live_cli' and v['worker_id']==bindings[0]['worker_id']]
    matches=[x for x in logs if any(0<=t-int(x['timestamp'])/1e6<=21600 for t in timestamps)]
    if len(matches)!=1:return {'status':'no_unique_time_correlated_dispatch','evidence':result}
    raw=root/('gateway-log-'+digest(result)[:16]+'.json');atomic_json(raw,result)
    receipt={'run_id':run_id,'cron_name':name,'scheduled_at':matches[0]['timestamp'],
        'worker_id':bindings[0]['worker_id'],'topic_id':bindings[0]['topic_id'],'raw_log_path':str(raw),
        'correlation_limit':'date/phase and six-hour window; current binding, not unique dispatch-id proof'}
    p=root/('input-dispatch-'+digest(receipt)[:16]+'.json');atomic_json(p,receipt)
    return {'status':'correlated_for_independent_audit','record':attach(state_dir,run_id,'dispatch',p)}
