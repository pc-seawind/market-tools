"""Investment-agent inbox and validated append-only review writeback.
No inferred semantic signoff. Invoked by existing daily/weekly report consumers.
"""
import argparse
import fcntl
import json
from datetime import timedelta
from pathlib import Path
from .ta_research import DEFAULT_ROOT, read, save, digest, now, verify_manifest, evidence_errors
from .timing import instant
from .timing_cli import file_hash
from .data import atomic_json

OWNER='investment-agent:existing-daily-and-weekend'
CATEGORIES=('data','logic','timing','pricing','unexpected_event')


def pointers(root):
    root=Path(root);found={}
    for p in list(root.glob('revisions/*/latest.json'))+[root/'seed.json',root/'latest.json']:
        if not p.exists():continue
        r=read(p);mp=Path(r['manifest'])
        if file_hash(mp)!=r['sha256']:raise ValueError('workflow_manifest_hash')
        verify_manifest(mp);found[r['sha256']]={**r,'manifest':str(mp.resolve())}
    return list(found.values())


def decisions(root,manifest_hash,code,asof):
    path=Path(root)/'workflow'/'followups'/manifest_hash/code
    entries=[read(p) for p in path.glob('*.json')]
    entries=[r for r in entries if instant(r['reviewed_at'])<=instant(asof)]
    entries.sort(key=lambda r:(instant(r['reviewed_at']),digest(r)))
    return entries[-1] if entries else None


def apply(batch_path,root=DEFAULT_ROOT):
    """Validate whole batch BEFORE writes. Persist per-item immutable envelopes.
    Confirm/refute needs post-prediction original business evidence, never price.
    """
    root=Path(root);batch=read(batch_path);at=batch['reviewed_at'];next_at=batch['next_check_at']
    if not batch.get('reviewer') or batch.get('owner')!=OWNER:raise ValueError('review_responsibility_required')
    if instant(at)>instant(now()) or instant(next_at)<=instant(at):raise ValueError('invalid_review_clock')
    known={p['sha256']:p for p in pointers(root)};pending=[]
    for item in batch['items']:
        mh=item['manifest_hash']
        if mh not in known:raise ValueError('unknown_review_manifest')
        ptr=known[mh];mp=Path(ptr['manifest']);frozen=read(mp.parent/'input.json')
        if instant(at)<instant(frozen['as_of']):raise ValueError('review_before_prediction')
        envelope={**item,'owner':OWNER,'reviewer':batch['reviewer'],'reviewed_at':at,'next_check_at':next_at,'manifest':str(mp)}
        if item['kind']=='quality':
            from .ta_quality import checked_review
            from .ta_audit import audit
            q=checked_review(mp,item['quality_path'])
            bad={s['code'] for s in audit(mp)['stocks'] if s['status']!='pass'}
            if any(s['status']=='pass' and s['code'] in bad for s in q['stocks']):raise ValueError('quality_protocol_blocked')
            envelope.update(quality=q,quality_hash=file_hash(item['quality_path']))
            target=root/'workflow'/'quality'/mh/(digest(envelope)+'.json')
        elif item['kind']=='followup':
            stock=next((s for s in frozen['stocks'] if s['code']==item['code']),None)
            if not stock:raise ValueError('foreign_followup_stock')
            es=item.get('post_evidence',[]);ids={e['evidence_id'] for e in es}
            for e in es:
                if e.get('code')!=item['code'] or evidence_errors(e,at) or instant(e['published_at'])<=instant(frozen['as_of']):raise ValueError('invalid_new_company_evidence')
            from .ta_review import evaluate
            evaluate(frozen['as_of'],at,[],{},es,item['proposition_checks'])
            short=item['short_term'];status=short['status']
            if status not in ('not_matured','blocked','confirmed','refuted','not_testable'):raise ValueError('invalid_short_review')
            close=stock.get('target_session');close=close+('T16:10:00+08:00' if stock['market']=='HK' else 'T15:10:00+08:00') if close else None
            if status in ('confirmed','refuted'):
                if not close or instant(at)<instant(close):raise ValueError('short_review_not_matured')
                if not short.get('evidence_ids') or any(e not in ids for e in short['evidence_ids']):raise ValueError('short_post_evidence_required')
                cited=[e for e in es if e['evidence_id'] in short['evidence_ids']]
                if not any(e['kind'] in ('quote','company_primary') and instant(e['published_at']).astimezone(instant(close).tzinfo).date().isoformat()==stock['target_session'] for e in cited):raise ValueError('short_target_session_evidence_required')
            if status=='not_matured' and close and instant(at)>=instant(close):raise ValueError('short_due_cannot_remain_not_matured')
            if not short.get('rationale') or item.get('source_work',{}).get('status') not in ('completed','blocked','pending','superseded') or not item.get('source_work',{}).get('rationale'):raise ValueError('review_reason_required')
            for c in item['proposition_checks']:
                if not c.get('rationale') or c.get('reviewer')!=batch['reviewer']:raise ValueError('proposition_owner_reason_required')
            if not item['proposition_checks']:raise ValueError('explicit_pending_proposition_required')
            if set(item['error_categories'])!=set(CATEGORIES):raise ValueError('error_review_incomplete')
            for c in item['error_categories'].values():
                if c.get('status') not in ('unknown','identified') or not c.get('rationale'):raise ValueError('error_reason_required')
                if c['status']=='identified' and (not c.get('evidence_ids') or any(e not in ids for e in c['evidence_ids'])):raise ValueError('error_evidence_required')
            target=root/'workflow'/'followups'/mh/item['code']/(digest(envelope)+'.json')
        else:raise ValueError('invalid_workflow_item')
        pending.append((target,envelope))
    root.mkdir(parents=True,exist_ok=True)
    with (root/'workflow.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        records=[]
        for target,envelope in pending:
            reused=target.exists()
            if reused and read(target)!=envelope:raise ValueError('immutable_review_collision')
            if not reused:save(target,envelope)
            if envelope['kind']=='quality':
                # Immutable embedded quality rather than mutable external reference.
                qp=target.with_suffix('.quality');
                if not qp.exists():save(qp,envelope['quality'])
                lp=root/'latest.json';current=read(lp) if lp.exists() else {}
                if current.get('sha256')==envelope['manifest_hash']:
                    atomic_json(lp,{**current,'quality_review':str(qp.resolve()),'quality_hash':file_hash(qp),'quality_status':'reviewed_by_existing_investment_workflow'})
            records.append({'path':str(target.resolve()),'sha256':file_hash(target),'kind':envelope['kind'],'reused':reused})
        receipt={'batch_hash':file_hash(batch_path),'owner':OWNER,'reviewer':batch['reviewer'],'records':records,'not_published':True}
        save(root/'workflow'/'receipts'/(digest(receipt)+'.json'),receipt)
    return receipt


def evidence_inbox(root):
    """Read immutable evidence-only supplements without replacing model inputs.

    These records are source review, never model/quality approval. A bad record
    is quarantined locally so it cannot suppress other daily/weekly work.
    """
    from .ta_evidence import verify_collection
    rows = []
    for path in sorted((Path(root) / 'evidence-supplements').glob('*.json')):
        try:
            pointer = read(path)
            collection = Path(pointer['collection'])
            parent = Path(pointer['parent_collection'])
            if file_hash(collection) != pointer['sha256'] or file_hash(parent) != pointer['parent_sha256']:
                raise ValueError('supplement_hash_mismatch')
            data = verify_collection(read(collection))
            old = read(parent)
            if data.get('parent_collection_hash') != pointer['parent_sha256']:
                raise ValueError('supplement_parent_mismatch')
            codes = [s['code'] for s in data['stocks']]
            if len(codes) != len(set(codes)) or set(codes) != {s['code'] for s in old['stocks']}:
                raise ValueError('supplement_scope_mismatch')
            rows.append({'status': 'pending_independent_source_review',
                         'collection': str(collection), 'sha256': pointer['sha256'],
                         'parent_collection': str(parent), 'parent_sha256': pointer['parent_sha256'],
                         'work_id': pointer['work_id'], 'not_published': True,
                         'does_not_replace_frozen_model_evidence': True,
                         'stocks': [{'code': s['code'], 'research_coverage': s['coverage'],
                                     'evidence_ids': [e['evidence_id'] for e in s['evidence']],
                                     'terminal': s.get('terminal', {})} for s in data['stocks']]})
        except (OSError, ValueError, KeyError, TypeError) as exc:
            rows.append({'status': 'blocked_invalid_supplement', 'pointer': str(path),
                         'error': str(exc), 'not_published': True})
    # Only a validated direct child of the same work supersedes old source
    # review. Keep the old immutable record visible but not pending/current.
    for row in rows:
        if 'sha256' not in row:continue
        successors=[r['sha256'] for r in rows if r.get('work_id')==row['work_id']
                    and r.get('parent_sha256')==row['sha256'] and r.get('sha256')!=row['sha256']]
        if successors:
            row.update(status='superseded_by_source_revision',superseded_by=successors)
    return rows


def inbox(root=DEFAULT_ROOT,asof=None):
    root=Path(root);at=asof or now();rows=[]
    for ptr in pointers(root):
        mp=Path(ptr['manifest']);frozen=read(mp.parent/'input.json')
        quality=list((root/'workflow'/'quality'/ptr['sha256']).glob('*.json'))
        for stock in frozen['stocks']:
            prior=decisions(root,ptr['sha256'],stock['code'],at)
            rows.append({'run_id':ptr['run_id'],'manifest':str(mp),'manifest_hash':ptr['sha256'],'code':stock['code'],
                         'research_coverage':stock.get('research_coverage',{'status':'not_searched'}),
                         'research_coverage_hash':digest(stock.get('research_coverage',{'status':'not_searched'})),
                         'owner':OWNER,'quality_status':'recorded' if quality else 'pending_source_review',
                         'followup_status':'recorded' if prior else 'pending_evidence_judgment',
                         'next_check_at':prior['next_check_at'] if prior else at,
                         'due_now':not prior or instant(prior['next_check_at'])<=instant(at),
                         'last_review':prior,'tasks':['原文补采/catalog更新或明确缺口','逐源质量签审','短期到期判定','经营命题与错误分类分别回写'],
                         'writeback':'ta_workflow apply --batch FILE; next report-consumers.sh via TA10_REVIEW_FILE'})
    failed=[]
    for p in (root/'requests').glob('*/launch.json'):
        launch=read(p);state=read(p.parent/'status.json') if (p.parent/'status.json').exists() else {}
        if not state.get('status','').startswith('completed'):
            failed.append({'request_id':p.parent.name,'launch':launch,'worker':state,'owner':OWNER,'next_check_at':launch.get('next_retry_at',at),'recovery_entry':'python3 -m mt1.ta_workflow recover --request ID --reason REASON'})
    return {'as_of':at,'owner':OWNER,'items':rows,'requests':failed,
            'evidence_supplements':evidence_inbox(root),
            'writeback_contract':'docs/ta10/R3-WORKFLOW.md','not_published':True}


def report_inbox(out,asof=None,root=DEFAULT_ROOT):
    result=inbox(root,asof);p=Path(out)/'research-work-inbox.json';save(p,result)
    return {'path':str(p),'sha256':file_hash(p),'count':len(result['items']),'owner':OWNER,'pending_requests':len(result['requests'])}


def recover(root,key,reason):
    from .ta_pipeline import request_refresh
    if not reason.strip():raise ValueError('recovery_reason_required')
    task=read(Path(root)/'requests'/key/'input.json')
    return request_refresh(task['identity']['report'],'evening',root,task['scope'],task['catalog'],recover_reason=reason,model_codes=task['identity'].get('model_codes'))


def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['inbox','apply','recover']);p.add_argument('--root',default=str(DEFAULT_ROOT));p.add_argument('--batch');p.add_argument('--request');p.add_argument('--reason');a=p.parse_args()
    result=inbox(a.root) if a.action=='inbox' else apply(a.batch,a.root) if a.action=='apply' else recover(a.root,a.request,a.reason or '')
    print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__':main()
