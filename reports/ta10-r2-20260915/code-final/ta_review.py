"""Layered follow-up review using NEW evidence, never price-only causal claims.
Appends to the EXISTING longitudinal archive via job=ta10-research, preserving
original prediction bytes/IDs. No model feedback automatically changes rules.
"""
import argparse
from datetime import datetime
from pathlib import Path
from .ta_research import read, digest, verify_manifest, evidence_errors, review_pending
from .timing import instant
from .longitudinal import archive, DEFAULT_ROOT, stable_id


def evaluate(prediction_at, asof, sessions, observations, post_evidence, proposition_checks, calendar_evidence=None):
    """No inferred sessions or return input scalar. Explicit typed panel required.
    A panel is an independently collected deterministic price-series envelope;
    raw hash/evidence identity, basis, timestamps and exact row values are checked.
    It remains a price observation, not a cash/dividend/factor P&L attribution.
    """
    if instant(asof)<instant(prediction_at):raise ValueError('review_before_prediction')
    if sessions!=sorted(set(sessions)):raise ValueError('duplicate_or_unsorted_sessions')
    if sessions:
        if not calendar_evidence or calendar_evidence.get('kind')!='calendar':raise ValueError('calendar_evidence_required')
        if evidence_errors(calendar_evidence,asof) or read(calendar_evidence['raw_path'])!=calendar_evidence['content']:raise ValueError('calendar_source_invalid')
        verified={d['close_at'] for d in calendar_evidence['content']['days'] if d['is_open'] is True}
        if any(d not in verified for d in sessions):raise ValueError('unverified_or_closed_session')
    completed=[d for d in sessions if instant(prediction_at)<instant(d)<=instant(asof)]
    evidence={e['evidence_id']:e for e in post_evidence}
    for e in post_evidence:
        if evidence_errors(e,asof) or e['kind']=='hypothesis' or instant(e['published_at'])<=instant(prediction_at):
            raise ValueError('review_requires_new_evidence')
    checks=[]
    for c in proposition_checks:
        if c['status'] not in ('confirmed','refuted','blocked','not_matured'):raise ValueError('invalid_proposition_status')
        if c['status'] in ('confirmed','refuted'):
            if not c.get('evidence_ids') or any(e not in evidence for e in c['evidence_ids']):raise ValueError('missing_post_evidence')
            if any(evidence[e]['kind'] not in ('company_primary','financial') for e in c['evidence_ids']):raise ValueError('price_cannot_prove_business')
            if not c.get('reviewer') or not c.get('rationale'):raise ValueError('attributed_review_required')
        checks.append(c)
    horizons=[]
    for n in (20,40,60):
        # Entry=first future completed close; n subsequent sessions (no off-by-one).
        status='not_matured' if len(completed)<=n else 'blocked'
        record={'trading_sessions':n,'status':status,'return':None,'excess_vs_market':None}
        obs=observations.get(str(n))
        if len(completed)>n and obs:
            panel=evidence.get(obs.get('price_panel_id'),{})
            if panel.get('kind')!='price_panel':raise ValueError('price_panel_required')
            data=panel['content']
            # Re-read the exact series, not an asserted scalar return or self-score.
            if read(panel['raw_path'])!=data:raise ValueError('price_panel_not_raw')
            if data.get('basis')!='unadjusted_close_price_only':raise ValueError('unsupported_return_basis')
            rows=data['rows'];by={r['close_at']:r for r in rows}
            if len(by)!=len(rows):raise ValueError('duplicate_price_rows')
            import math
            a,b=by[completed[0]],by[completed[n]]
            for row in (a,b):
                if row['code']!=data['code'] or row['currency']!=data['currency'] or not math.isfinite(float(row['close'])) or float(row['close'])<=0:raise ValueError('price_identity_numeric_error')
            value=float(b['close'])/float(a['close'])-1
            record.update(status='observed_price_only',return_value=value,entry_close_at=completed[0],exit_close_at=completed[n],source=panel['evidence_id'],limitation='unadjusted_price_only_NOT_total_return_or_personal_PnL')
            record['return']=value
        horizons.append(record)
    return {'horizons':horizons,'business_propositions':checks,'business_status':'blocked' if not checks else 'reviewed_not_automatic_semantic_certification',
            'return_decomposition':{'market':None,'industry':None,'style':None,'residual':None,'status':'blocked_factor_model_missing','residual_is_company_causality':False},
            'error_categories':{k:'unknown' for k in ('data','logic','timing','pricing','unexpected_event')},'rule_mutation':False}


def _register(manifest,root=DEFAULT_ROOT):
    """Publish exact inputs/results into existing weekly-visible evidence archive.
    Idempotence is supplied by longitudinal.archive content identity.
    """
    mp=Path(manifest);m=verify_manifest(mp);frozen=read(mp.parent/'input.json');result=read(mp.parent/'results.json')
    mats=[{'name':'input.json','path':mp.parent/'input.json'}, {'name':'results.json','path':mp.parent/'results.json'}, {'name':'manifest.json','path':mp}]
    events=[]
    for stock in result['stocks']:
        code=stock['code'];name=code+'-result.json';mats.append({'name':name,'path':mp.parent/code/'result.json'})
        output=stock.get('calls',{}).get('C',{}).get('output') or {}
        origin=result['run_id']+':'+code
        events.append({'kind':'company_research','origin_id':origin,'status':'blocked' if stock['status']=='blocked' else 'not_matured',
                       'original_judgment':output.get('adjudication'),'original_date':result['as_of'][:10],
                       'published_at':result['as_of'],'verification_target':'short_session_and_20_40_60_separate_business_propositions',
                       'next_review_date':output.get('next_review_date'),'forecast_material':name,
                       'original_prediction_run':result['run_id'],'errors':stock.get('blockers',[])})
    for f in m['files']:
        if '/' in f['path']:
            mats.append({'name':f['path'].replace('/','--'),'path':mp.parent/f['path']})
    # Raw inputs retained in shared durable archive, not merely URL/hash pointers.
    seen=set()
    for s in frozen['stocks']:
        for e in s['evidence']:
            if e['raw_sha256'] in seen:continue
            seen.add(e['raw_sha256'])
            if e.get('text_path'):mats.append({'name':e['evidence_id']+'-text','path':e['text_path']})
            mats.append({'name':e['evidence_id']+'-raw','path':e['raw_path']})
    raw_root=Path(frozen['stocks'][0]['evidence'][0]['raw_path']).parent
    for p in (raw_root/'inputs').glob('calendar*.json'):
        mats.append({'name':p.name,'path':p})
    mats=[{**m, 'path':str(m['path'])} for m in mats]
    return archive(root=root,job='ta10-research',run_id=result['run_id'],trade_date=result['as_of'][:10],
                   scope_epoch=result['scope_epoch'],result=result,materials=mats,events=events)


def register(manifest,root=DEFAULT_ROOT):
    import fcntl
    from .longitudinal import manifests
    from .timing_cli import file_hash
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    mp=Path(manifest);m=verify_manifest(mp);source_hash=file_hash(mp)
    with (root/'.ta10-registration.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        # Recovery after archive commit but before local pointer: find exact
        # committed source identity instead of appending a duplicate revision.
        for _,path,old in manifests(root):
            if old['job']!='ta10-research' or old['run_id']!=m['run_id']:continue
            if not any(x['name']=='manifest.json' and x.get('sha256')==source_hash for x in old['materials']):continue
            base=Path(path).parent
            if file_hash(base/old['result']['path'])!=old['result']['sha256']:raise ValueError('registered_result_corrupt')
            for x in old['materials']:
                if x.get('status')=='archived' and file_hash(base/x['blob'])!=x['sha256']:raise ValueError('registered_material_corrupt')
            return {'manifest':path,'sha256':file_hash(path),'revision':old['revision']}
        return _register(manifest,root)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--manifest',required=True);p.add_argument('--archive-root',default=str(DEFAULT_ROOT));a=p.parse_args()
    import json
    print(json.dumps(register(a.manifest,a.archive_root),ensure_ascii=False))
