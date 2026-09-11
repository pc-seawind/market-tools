"""Single deterministic post-research command: evidence events, diff, dry-run sync.

The agent prepares one review bundle, not shell pipelines. Each event has its own
idempotency key and expected version. Rejected events do not roll back independent
valid research. Never executes third-party writes or rewrites legacy logs.
"""
from datetime import datetime, timezone, date
from pathlib import Path
from .data import atomic_json, cn_calendar, foreign_calendar
from .calendar import gate
from .plans import reduce_plan, eligible
from .methods import reduce_method
from .store import Store, digest


def finalize(bundle, state_dir, investment_dir):
    if bundle.get('phase') not in ('morning','evening','saturday','sunday') or not bundle.get('reviewer'):
        raise ValueError('phase and reviewer required')
    from .evidence import validate_source, capture_review
    validate_source(state_dir,bundle)
    now=datetime.now(timezone.utc)
    store=Store(Path(state_dir)/'plans.db'); errors=[]; changes=[]; methods=[]; gates={}
    try:
        reviewed=set(bundle.get('reviewed_plan_ids',[]))
        pending=[]
        for item in bundle.get('review_items',[]):
            p=store.latest('plan:'+item.get('plan_id',''))
            from .scope import filter_plans
            if p and not filter_plans([p]):
                raise ValueError('pending_review_outside_tracking_scope')
            if not p or p['version']!=item.get('expected_version'):
                raise ValueError('review item missing or stale version')
            if item.get('status')!='pending' or not item.get('note'):
                raise ValueError('review_items records pending gaps only; validated decisions use plan_events')
            if p['plan_id'] in reviewed or p['plan_id'] in {e['id'] for e in bundle.get('plan_events',[])}:
                raise ValueError('pending item cannot count as reviewed')
            pending.append({'plan':p,'note':item['note']})
        for event in bundle.get('method_events',[]):
            try:
                if event['payload'].get('method_id',event['id'])!=event['id']: raise ValueError('method id mismatch')
                methods.append(store.apply('method:'+event['id'],event['expected_version'],event['request_id'],event['payload'],event['reason'],reduce_method))
            except Exception as e: errors.append({'event':event.get('request_id'),'error':str(e)})
        for event in bundle.get('plan_events',[]):
            try:
                old=store.latest('plan:'+event['id'])
                payload=event['payload']
                if payload.get('plan_id',event['id'])!=event['id']: raise ValueError('plan id mismatch')
                new={**(old or {}),**payload}
                if new.get('state')=='BUY' or new.get('unheld_direction')=='BUY':
                    market=new['market']
                    if market not in gates:
                        cal=cn_calendar(now) if market=='CN' else foreign_calendar(now,market)
                        # Weekend research is allowed, but must not bypass the
                        # market-open gate for a new BUY. Evening additionally
                        # requires the CN session to have completed.
                        buy_phase='evening' if bundle['phase']=='evening' else 'morning'
                        gates[market]=gate(cal,market,buy_phase,now)
                    if not gates[market]['allowed']: raise ValueError('market gate closed')
                result=store.apply('plan:'+event['id'],event['expected_version'],event['request_id'],payload,event['reason'],lambda old, patch: reduce_plan(old, patch, store.latest))
                changes.append({'before':old,'after':result,'reason':event['reason'],'request_id':event['request_id']})
            except Exception as e: errors.append({'event':event.get('request_id'),'error':str(e)})
        reviewed=set(bundle.get('reviewed_plan_ids',[]))
        actual={p['plan_id'] for p in store.all()}
        if reviewed-actual: errors.append({'error':'unknown reviewed plan ids','ids':sorted(reviewed-actual)})
        research=bundle.get('research',{})
        research_ok=(research.get('fetch_status')=='ok' and bool(research.get('sources')))
        if research_ok:
            import hashlib
            for s in research['sources']:
                try:
                    path=Path(s['content_path'])
                    if not (s.get('url') and s.get('fetched_at') and
                            hashlib.sha256(path.read_bytes()).hexdigest()==s['content_hash']): research_ok=False
                except (OSError,KeyError,TypeError):research_ok=False
        summary={'phase':bundle['phase'],'reviewer':bundle['reviewer'],'changes':changes,'methods':methods,
                 'reviewed_plan_ids':sorted(reviewed & actual),'unreviewed_plan_ids':sorted(actual-reviewed),
                 'research_status':'fetched' if research_ok else 'not_verified',
                 'research':research,'errors':errors,'source_run_id':bundle.get('source_run_id'),
                 'pending_review_ids':[v['plan']['plan_id'] for v in pending],'watchlist':{'mode':'dry_run','pending':[
                   {'code':p['code'],'plan_id':p['plan_id'],'version':p['version']}
                   for p in store.all() if eligible(p,date.today(),store.latest)]},
                 'legacy_rec_projection':'not_written; structured ledger is authoritative for MT-1.0'}
        identity=digest(bundle)
        # Immutable result per attempt, including retries after isolated errors.
        suffix=now.strftime('%Y%m%d-%H%M%S%f')
        out=Path(state_dir)/'reviews'/f'{identity[:16]}-{suffix}.json';atomic_json(out,summary)
        report=Path(investment_dir)/'reference/medium-term-reviews'/f'{date.today()}-{bundle["phase"]}-review-{suffix}.md'
        report.parent.mkdir(parents=True,exist_ok=True)
        lines=['**MT-1.0 证据审查结果**','',f'审查人：{bundle["reviewer"]}；覆盖 {len(reviewed & actual)}/{len(actual)}。',
               '|标的|未持有者方向|已有持仓方向|变化理由|下次复核|','|---|---|---|---|---|']
        if bundle.get('report_context'):
            lines.insert(1,str(bundle['report_context']))
        for c in changes:
            p=c['after']; reason=c['reason'].replace('|','/').replace('\n',' ')
            lines.append(f"|{p['code']}|{p['unheld_direction']}|{p['held_direction']}|{reason}|{p.get('review_due') or '未知'}|")
        for item in pending:
            p=item['plan']; note=item['note'].replace('|','/').replace('\n',' ')
            lines.append(f"|{p['code']}|{p['unheld_direction']}|{p['held_direction']}|待复核：{note}|{p.get('review_due') or '未知'}|")
        lines.append('')
        lines.append('运行关联：`'+str(bundle.get('source_run_id') or '未关联')+'`')
        if not changes:lines.append('无新增状态事件；不代表未审查的计划已经验证。')
        lines.extend(['',f"联网：{summary['research_status']}；自选：仅 dry-run。",'异常：'+str(errors),'数据：`'+str(out)+'`'])
        with report.open('x') as f:f.write('\n'.join(lines)+'\n')
        result={**summary,'report_path':str(report),'result_json':str(out)}
        capture_review(state_dir,bundle,result)
        return result
    finally:store.close()
