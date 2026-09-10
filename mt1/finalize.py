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
    now=datetime.now(timezone.utc)
    store=Store(Path(state_dir)/'plans.db'); errors=[]; changes=[]; methods=[]; gates={}
    try:
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
                        gates[market]=gate(cal,market,'morning',now)
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
                 'research':research,'errors':errors,'watchlist':{'mode':'dry_run','pending':[
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
        for c in changes:
            p=c['after']; reason=c['reason'].replace('|','/').replace('\n',' ')
            lines.append(f"|{p['code']}|{p['unheld_direction']}|{p['held_direction']}|{reason}|{p.get('review_due') or '未知'}|")
        if not changes:lines.append('无新增状态事件；不代表未审查的计划已经验证。')
        lines.extend(['',f"联网：{summary['research_status']}；自选：仅 dry-run。",'异常：'+str(errors),'数据：`'+str(out)+'`'])
        with report.open('x') as f:f.write('\n'.join(lines)+'\n')
        return {**summary,'report_path':str(report),'result_json':str(out)}
    finally:store.close()
