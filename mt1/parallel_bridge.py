"""Read-only plan binding + immutable review observations, no ledger/trade mutation."""
import gzip
import hashlib
import json
import sqlite3
from datetime import datetime,timezone
from pathlib import Path
from .review_time import instant,CN
from .candidates import day


def ledger_snapshot(path):
    path=Path(path)
    if not path.exists():raise ValueError('authoritative ledger missing')
    con=sqlite3.connect('file:'+str(path.resolve())+'?mode=ro',uri=True)
    try:
        plans=[json.loads(r[0]) for r in con.execute("SELECT after_json FROM events e WHERE entity LIKE 'plan:%' AND version=(SELECT MAX(version) FROM events WHERE entity=e.entity)")]
    finally:con.close()
    from .scope import filter_plans
    return filter_plans(plans)


def bind_plan(p, decision_at):
    now=instant(decision_at).astimezone(CN).date()
    immutable={k:p.get(k) for k in ('plan_id','version','code','episode','original_date','original_reason','original_deadline','reference_price','deadline_extension')}
    held=p.get('holding_status','unknown')
    if held=='confirmed' and not p.get('holding_evidence'):held='unknown'
    effective=(p.get('deadline_extension') or {}).get('deadline') or p.get('original_deadline')
    status='unknown' if not effective else 'due' if day(effective)<=now else 'within_term'
    action='持仓未确认，不能生成实盘持有动作' if held=='unknown' else '未持有' if held=='not_held' else '持有复核'
    if p.get('state')=='EXIT':action='原计划已退出；不自动重入'
    elif held=='confirmed' and status=='due':action='期限到期，减仓/退出复核；不得自动延期'
    elif held=='confirmed' and status=='unknown':action='原期限未知，先重建，不自动延期'
    return {'bound_fields':immutable,'holding_status':held,'effective_deadline':effective,'deadline_status':status,
            'held_action':action,'original_state':p.get('state'),'actual_cost':p.get('actual_cost') if held=='confirmed' else None}


def close_review(result_dir, ledger_path, *, batch_size=10):
    """Bind every legacy security, even if not discovered; exclude metadata only."""
    from .parallel import review_gate
    root=Path(result_dir);summary=json.loads((root/'summary.json').read_text())
    decision=summary['decision_at'];price=summary['asof']
    with gzip.open(root/'funnel.json.gz','rt') as f:records=json.load(f)
    by={r['code']:r for r in records}
    packets=json.loads((root/'review-input.json').read_text())
    plans=ledger_snapshot(ledger_path)
    observations=[]
    for p in plans:
        if p['code'].startswith('_'):continue
        bound=bind_plan(p,decision);r=by.get(p['code'])
        reviewed=[]
        for packet in packets:
            if packet['code']==p['code']:
                reviewed.append({'channel':packet['channel'],'kind':packet['kind'],
                    'gate':review_gate(packet,p['code'],packet['channel'],price,packet['kind'],decision_at=decision)})
        observations.append({'code':p['code'],'binding':bound,'price_asof':price,'decision_at':decision,
            'channels':r['channels'] if r else [],'discovery_admitted':bool(r and r['channels']),
            'review_input_results':reviewed,'conditional_plan':r['plan'] if r else None,
            'proposal_type':'review_observation_only','final':False,'trade_intent':False,
            'remaining_blocks':(['not_in_current_discovery'] if not r or not r['channels'] else [])+
                (['holding_confirmation_missing'] if bound['holding_status']=='unknown' else [])+
                (['original_deadline_missing'] if bound['deadline_status']=='unknown' else [])+['method_shadow_no_final']})
    # Freeze research source bytes. Hash identifies the note vs primary filing;
    # a secondary note must never be advertised as downloaded primary evidence.
    sources=root/'review-sources';sources.mkdir(exist_ok=False)
    for packet in packets:
        for src in packet['sources']:
            raw=Path(src['path']).read_bytes();h=hashlib.sha256(raw).hexdigest()
            if h!=src['sha256']:raise ValueError('research source changed after review')
            dest=sources/h
            if not dest.exists():dest.write_bytes(raw)
    snap=json.dumps(plans,ensure_ascii=False,sort_keys=True).encode()
    (root/'bound-ledger-snapshot.json').write_bytes(snap)
    from .scope import counts, load
    scope=load()
    receipt={'tracking_scope':counts(scope),
             'holdings_without_plan':sorted(set(scope['holdings'])-{p['code'] for p in plans}) if scope else [],
             'decision_at':decision,'price_asof':price,'ledger_path':str(Path(ledger_path).resolve()),
             'ledger_snapshot_sha256':hashlib.sha256(snap).hexdigest(),'observations':observations,
             'writes':'immutable observations only; authoritative ledger untouched','method_status':'shadow'}
    (root/'bound-review.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2))
    lines=['**研究输入—时间—条件—原计划绑定闭环（非交易）**','',f'行情日 {price}；决策时间 {decision}。',
           f'范围 {counts(scope)}；真实账本绑定 {len(observations)} 项；成本/期限未知保留未知，不能伪造确认持仓。',
           '|代码|全部发现通道|研究包结果|持仓绑定|原期限|当前条件/卡点|','|---|---|---|---|---|---|']
    for o in observations:
        if not o['review_input_results']:continue
        b=o['binding'];p=o['conditional_plan'] or {}
        lines.append(f"|{o['code']}|{','.join(o['channels']) or '未进入发现池'}|{'; '.join(v['channel']+':'+v['gate']['status'] for v in o['review_input_results'])}|{b['held_action']}|{b['bound_fields']['original_deadline'] or '未知'}|{p.get('unheld','未发现，不放行')}；{','.join(o['remaining_blocks'])}|")
    lines+=['','原有EXIT保持退出。期限/参考价/版本逐项冻结，未创建或改写计划；全部观察不能直接送交易/自选接口。','研究包中partial代表真实材料已接入但剩余研究未完成，不改写成pass。']
    (root/'bound-review.md').write_text('\n'.join(lines)+'\n')
    return {'bound_security_plans':len(observations),'reviewed_input_codes':sorted({o['code'] for o in observations if o['review_input_results']}),
            'confirmed_holdings':sum(o['binding']['holding_status']=='confirmed' for o in observations),'report':str(root/'bound-review.md')}


def assert_binding_current(observation,ledger_path):
    """Call immediately before consuming a reviewed proposal; stale binding fails."""
    bound=observation['binding']['bound_fields']
    current=next((p for p in ledger_snapshot(ledger_path) if p['plan_id']==bound['plan_id']),None)
    if not current or any(current.get(k)!=v for k,v in bound.items()):raise ValueError('stale or altered plan binding')
    return True
