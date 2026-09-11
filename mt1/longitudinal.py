"""Durable byte-preserving daily evidence and derived cross-week review index.

No network, trading, source mutation, evidence deletion or strategy evaluation.
Only committed manifests are visible; latest/index are disposable projections.
"""
import fcntl
import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone, date
from pathlib import Path
from .data import atomic_json

DEFAULT_ROOT = Path(__file__).resolve().parents[1] / '.cron_state/mt1/longitudinal'
TERMINAL = {'confirmed', 'refuted', 'closed', 'archived'}
STATUSES = TERMINAL | {'pending', 'not_matured', 'blocked', 'failed'}
IMMUTABLE = ('kind', 'origin_id', 'scope_epoch', 'original_judgment', 'original_date',
             'reference_price', 'entry_condition', 'exit_reason', 'published_at', 'target_at',
             'forecast_material', 'forecast_sha256', 'verification_target', 'due_date', 'revises')


def stable_id(kind, origin_id, scope_epoch):
    if not all(isinstance(v, str) and v for v in (kind, origin_id, scope_epoch)):
        raise ValueError('stable ID requires kind, original source ID, scope epoch')
    return kind + ':' + hashlib.sha256(json.dumps([kind, origin_id, scope_epoch]).encode()).hexdigest()[:24]


def _part(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,160}', value) or value in ('.', '..'):
        raise ValueError('unsafe archive path component')
    return value


def manifests(root):
    records=[]
    for p in Path(root).glob('*/*/*/*/r*/manifest.json'):
        v=json.loads(p.read_text())
        records.append((v['recorded_at'],str(p),v))
    return sorted(records)


def _latest_events(records):
    events={}
    for _,path,record in records:
        for e in record.get('events',[]):
            events[e['entity_id']]={**e,'manifest':path,'recorded_at':record['recorded_at']}
    return events


def _validate_events(events, previous, material_names, epoch):
    seen=set()
    for e in events:
        eid=stable_id(e['kind'],e['origin_id'],epoch)
        if e.get('entity_id',eid)!=eid or eid in seen:
            raise ValueError('wrong or duplicate stable entity ID')
        e.update(entity_id=eid,scope_epoch=epoch)
        seen.add(eid)
        if e.get('status') not in STATUSES:
            raise ValueError('explicit pending/blocked/outcome status required')
        for key in ('due_date','next_review_date'):
            if e.get(key): date.fromisoformat(e[key])
        if e.get('kind')=='forecast':
            published=datetime.fromisoformat(e['published_at'])
            target=datetime.fromisoformat(e['target_at'])
            if not published.tzinfo or not target.tzinfo or published>=target:
                raise ValueError('forecast must predate target session')
            if e.get('forecast_material') not in material_names:
                raise ValueError('forecast original bytes required; URL/hash is insufficient')
            e['forecast_sha256']=material_names[e['forecast_material']]
        old=previous.get(eid)
        if old:
            for field in IMMUTABLE:
                if e.get(field)!=old.get(field):
                    raise ValueError('original event immutable: '+field)
        if e['status'] in ('confirmed','refuted') and not e.get('observation_materials'):
            raise ValueError('verification outcome requires observation material')
        if any(n not in material_names for n in e.get('observation_materials',[])):
            raise ValueError('observation material missing')


def _write_new(path, raw):
    with Path(path).open('xb') as f:
        f.write(raw); f.flush(); os.fsync(f.fileno())


def archive(*, root=DEFAULT_ROOT, job, run_id, trade_date, scope_epoch, result,
            materials=(), events=()):
    """Copy real content before a caller overwrites /tmp/handoff or publishes.

    materials: name + path or content; inaccessible items require status/reason.
    URL-only inputs are recorded as unavailable, never as archived source text.
    """
    date.fromisoformat(trade_date)
    parts=[_part(v) for v in (job,scope_epoch,trade_date,run_id)]
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    with (root/'.archive.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        existing=manifests(root)
        events=json.loads(json.dumps(list(events)))
        contents=[];names=set()
        for item in materials:
            item=dict(item);name=_part(item['name'])
            if name in names:raise ValueError('duplicate material name')
            names.add(name)
            raw=item.pop('content',None)
            try:
                if raw is None and item.get('path'):raw=Path(item['path']).read_bytes()
                if isinstance(raw,str):raw=raw.encode()
                if raw is None:raise ValueError(item.get('reason','source content unavailable'))
                if not isinstance(raw,bytes):raise ValueError('material must be bytes or text')
                if not raw:raise ValueError('empty material is not archived source content')
                item.update(status='archived',sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw),blob='materials/'+name)
            except (OSError,ValueError) as e:
                raw=None;item.update(status='unavailable',reason=str(e),blob=None)
            contents.append((item,raw))
        available={i['name']:i['sha256'] for i,raw in contents if raw is not None}
        _validate_events(events,_latest_events(existing),available,scope_epoch)
        parent=root.joinpath(*parts);parent.mkdir(parents=True,exist_ok=True)
        revision=max([int(p.name[1:]) for p in parent.glob('r*') if p.is_dir() and p.name[1:].isdigit()]+[0])+1
        directory=parent/f'r{revision:06}';directory.mkdir()
        (directory/'materials').mkdir()
        for item,raw in contents:
            if raw is not None:
                _write_new(directory/item['blob'],raw)
        output=json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False).encode()
        _write_new(directory/'result.json',output)
        now=datetime.now(timezone.utc).isoformat()
        try:
            head=subprocess.check_output(['git','-C',str(Path(__file__).resolve().parents[1]),'rev-parse','HEAD'],text=True,timeout=5).strip()
        except (OSError,subprocess.SubprocessError):head='unavailable'
        record={'schema_version':1,'job':job,'run_id':run_id,'trade_date':trade_date,'scope_epoch':scope_epoch,
                'revision':revision,'recorded_at':now,'git_head':head,
                'materials':[i for i,raw in contents],'events':events,
                'result':{'path':'result.json','sha256':hashlib.sha256(output).hexdigest()},
                'status':result.get('status','recorded'),
                'missing_materials':[i['name'] for i,raw in contents if raw is None],
                'strategy_evaluation':'weekly_only; daily_collection_not_verdict'}
        # Manifest commit point: incomplete dirs after a crash never enter indexes.
        atomic_json(directory/'manifest.json',record)
        receipt={'manifest':str(directory/'manifest.json'),'revision':revision,
                 'sha256':hashlib.sha256((directory/'manifest.json').read_bytes()).hexdigest()}
        atomic_json(root/'latest.json',receipt)
        return receipt


def weekly_index(root=DEFAULT_ROOT, *, asof=None, current_epoch=None):
    """Rebuild from immutable daily manifests; no week boundary drops pending."""
    asof=asof or date.today().isoformat();date.fromisoformat(asof)
    records=[r for r in manifests(root) if r[2]['recorded_at'][:10]<=asof]
    latest=_latest_events(records)
    events=[]
    for eid,e in sorted(latest.items()):
        next_date=e.get('next_review_date') or e.get('due_date')
        events.append({**e,'historical_epoch':bool(current_epoch and e['scope_epoch']!=current_epoch),
                       'overdue':bool(e['status'] not in TERMINAL and next_date and next_date<asof)})
    integrity=[]
    for _,path,v in records:
        parent=Path(path).parent
        refs=[{'blob':v['result']['path'],'sha256':v['result']['sha256']},*[m for m in v['materials'] if m.get('blob')]]
        for ref in refs:
            try:
                if hashlib.sha256((parent/ref['blob']).read_bytes()).hexdigest()!=ref['sha256']:
                    raise ValueError('archive bytes hash mismatch')
            except (OSError,ValueError) as error:
                integrity.append({'manifest':path,'blob':ref['blob'],'error':str(error)})
    return {'asof':asof,'integrity_errors':integrity,'current_epoch':current_epoch,'run_count':len(records),
            'events':events,'pending':[e for e in events if e['status'] not in TERMINAL],
            'closed_history':[e for e in events if e['status'] in TERMINAL],
            'forecast_originals':[{'manifest':p,'event':e} for _,p,v in records for e in v.get('events',[]) if e['kind']=='forecast'],
            'material_gaps':[{'manifest':p,'missing':v['missing_materials']} for _,p,v in records if v['missing_materials']],
            'failed_runs':[p for _,p,v in records if v['status'] in ('partial','failed')],
            'coverage':'observed_archives_only; expected_schedule_not_supplied',
            'strategy_evaluation':'weekly_review_required_not_automatically_completed'}
