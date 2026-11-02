"""Research work disposition, independent of security admission and monitoring.

Same append-only Store, separate entity/CAS; no company judgements or cron writes.
Legacy pending is a read-only unclassified projection, never an implicit migration.
"""
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from .store import Store
from .research_pool import instant, db_path

LIVE = {'active', 'review_required', 'unclassified'}
PARKED = {'waiting', 'blocked', 'archived'}
LABELS = {'active': '待补研', 'review_required': '强制处置复核', 'unclassified': '未分类待分流',
          'waiting': '等待外部事件', 'blocked': '数据受阻', 'archived': '搁置归档', 'completed': '真实完成'}


def timeline(path, epoch):
    p = db_path(path)
    if not p.exists():
        return []
    with sqlite3.connect('file:' + str(p.resolve()) + '?mode=ro', uri=True) as con:
        rows = con.execute("SELECT entity,after_json FROM events WHERE entity LIKE 'research:%' OR entity LIKE 'research-task:%' ORDER BY seq").fetchall()
    return [(entity.split(':', 1)[0], r) for entity, raw in rows
            if (r := json.loads(raw))['scope_epoch'] == epoch]


def _hashes(r):
    return {s['sha256'] for s in r.get('sources', [])}


def _seed(r, status):
    return {'code': r['code'], 'status': status, 'owner': 'investment', 'version': 0,
            'created_at': r['recorded_at'], 'last_attempt_at': r['reviewed_at'],
            'original_due': r.get('next_review'), 'due': r.get('next_review'),
            'open_gaps': [], 'legacy_gap': r.get('gap_or_exit_basis'),
            'next_action': None, 'no_progress': 0, 'attempts': 0, 'seen_hashes': [],
            'resolved_gaps': [], 'reopen_hashes': [], 'extensions': [], 'generation': 1}


def fold(entries, now):
    """Return task rows and exact membership changes, including due-date wakes."""
    tasks, research, changes = {}, {}, []
    def change(code, before, after, at, kind, request_id=None):
        changes.append({'code': code, 'before': before, 'after': after, 'at': at,
                        'kind': kind, 'request_id': request_id})
    def expire(until):
        for code, t in tasks.items():
            if t['status'] in {'waiting', 'blocked'} and instant(t['check_at']) <= until:
                before = t['status']
                t.update(status='review_required', parked_from=before)
                change(code, before, t['status'], t['check_at'], 'reopened_check')
        for code, r in research.items():
            if r['state'] in {'watch', 'recommended'} and r.get('next_review') and instant(r['next_review']) <= until:
                t = tasks[code]
                # An explicit parked disposition is not undone by daily projection.
                if t['status'] == 'completed':
                    before = t['status']
                    t.update(status='unclassified', due=r['next_review'])
                    change(code, before, t['status'], r['next_review'], 'reopened_due')
    for kind, r in entries:
        at = instant(r['recorded_at'])
        if at > now:
            continue
        expire(at)
        code = r['code']
        old = tasks.get(code)
        before = old['status'] if old else None
        if kind == 'research-task':
            tasks[code] = {**r['task'], 'version': r['version']}
            change(code, before, tasks[code]['status'], r['recorded_at'], r['action'], r['request_id'])
            continue
        research[code] = r
        if r['state'] == 'pending':
            t = dict(old) if old else _seed(r, 'unclassified')
            if t['status'] == 'completed':
                t['status'] = 'unclassified'
            hashes = _hashes(r)
            progress = bool(hashes - set(t['seen_hashes']))
            t.update(seen_hashes=sorted(set(t['seen_hashes']) | hashes),
                     last_attempt_at=max(t['last_attempt_at'], r['reviewed_at'], key=instant), legacy_gap=r.get('gap_or_exit_basis'),
                     attempts=t['attempts'] + 1,
                     no_progress=0 if progress else t['no_progress'] + 1)
            if t['status'] in LIVE - {'review_required'} and t['no_progress'] >= 2:
                t.update(status='review_required', disposition_review_event_id=r['request_id'])
            # Old pending due remains the first known due, not the last refresh.
            if t['original_due'] is None and r.get('next_review'):
                t.update(original_due=r['next_review'], due=r['next_review'])
            tasks[code] = t
        else:
            if old is None:
                change(code, None, 'active', r['recorded_at'], 'started', r['request_id'])
                before = 'active'
            t = dict(old) if old else _seed(r, 'completed')
            t.update(status='completed', completion_state=r['state'], completion_event_id=r['request_id'],
                     seen_hashes=sorted(set(t['seen_hashes']) | _hashes(r)))
            tasks[code] = t
        after = tasks[code]['status']
        if before is None or before != after:
            change(code, before, after, r['recorded_at'], 'started' if before is None and after in LIVE else 'reopened' if after in LIVE else 'completed', r['request_id'])
    expire(now)
    for code, t in tasks.items():
        r = research.get(code)
        t['research_version'] = r['version'] if r else 0
        t['admission_state'] = r['state'] if r else None
        t['research_event_id'] = r['request_id'] if r else None
        t['effective_status'] = t['status']
        t['overdue'] = bool(t.get('due') and instant(t['due']) <= now and t['status'] in LIVE)
    return tasks, changes


def snapshot(path, epoch, now=None, since=None, entries=None):
    now = now or datetime.now(timezone.utc)
    since = instant(since) if isinstance(since, str) else since
    since = since or now.astimezone(ZoneInfo('Asia/Shanghai')).replace(hour=0, minute=0, second=0, microsecond=0)
    if since > now:
        raise ValueError('period start after asof')
    entries = timeline(path, epoch) if entries is None else entries
    tasks, changes = fold(entries, now)
    opening, _ = fold([(k, r) for k, r in entries if instant(r['recorded_at']) < since], since - timedelta(microseconds=1))
    transitions = [r for r in changes if since <= instant(r['at']) <= now]
    opening_count = sum(t['effective_status'] in LIVE for t in opening.values())
    closing_count = sum(t['effective_status'] in LIVE for t in tasks.values())
    flows = {'started': 0, 'reopened': 0, 'completed': 0, 'waiting': 0, 'blocked': 0, 'archived': 0}
    for r in transitions:
        before, after = r['before'], r['after']
        if before not in LIVE and after in LIVE:
            flows['started' if before is None else 'reopened'] += 1
        if before in LIVE and after not in LIVE:
            flows[after] += 1
    expected = opening_count + flows['started'] + flows['reopened'] - sum(flows[k] for k in ('completed', 'waiting', 'blocked', 'archived'))
    if expected != closing_count:
        raise ValueError('task accounting does not reconcile')
    counts = {s: sum(t['effective_status'] == s for t in tasks.values()) for s in LABELS}
    return {'asof': now.isoformat(), 'since': since.isoformat(), 'scope_epoch': epoch,
            'rows': list(tasks.values()), 'transitions': transitions,
            'counts': counts, 'accounting': {'opening_active': opening_count, **flows, 'closing_active': closing_count,
                'overdue': sum(t['overdue'] for t in tasks.values()), 'reconciled': True},
            'unresolved_total': sum(t['effective_status'] != 'completed' for t in tasks.values()),
            'parked_history': {s: len({r['code'] for k, r in entries if k == 'research-task' and instant(r['recorded_at']) <= now and r['task']['status'] == s}) for s in PARKED}}


def _evidence(path, sources, reviewed):
    hashes = set()
    for src in sources:
        if not src['url'].startswith(('https://', 'http://')) or not instant(src['published_at']) <= instant(src['fetched_at']) <= reviewed:
            raise ValueError('task evidence URL/time invalid')
        raw = Path(src['path']).read_bytes()
        h = hashlib.sha256(raw).hexdigest()
        if h != src['sha256']:
            raise ValueError('task source hash mismatch')
        dest = Path(str(path) + '.research-sources') / h
        dest.parent.mkdir(exist_ok=True)
        if dest.exists():
            if dest.read_bytes() != raw:
                raise ValueError('task source archive conflict')
        else:
            with dest.open('xb') as f:
                f.write(raw); f.flush(); os.fsync(f.fileno())
        hashes.add(h)
    return hashes


def apply(path, event, now=None):
    now = now or datetime.now(timezone.utc)
    path = Path(path); raw = path.read_bytes(); data = json.loads(raw)
    epoch = data.get('epoch') or data['scope_epoch']
    if event['scope_epoch'] != epoch or event['scope_sha256'] != hashlib.sha256(raw).hexdigest():
        raise ValueError('scope CAS/epoch conflict')
    if event.get('domain') != 'investment' or not event.get('reviewer') or not event.get('reason'):
        raise ValueError('investment reviewer/reason required')
    if not isinstance(event['code'], str) or not event['code'] or ':' in event['code']:
        raise ValueError('invalid code')
    reviewed = instant(event['reviewed_at'])
    if not instant(data['reset_at']) <= reviewed <= now or now - reviewed > timedelta(days=7):
        raise ValueError('stale/future/pre-reset task action')
    action = event['action']
    if action not in {'start', 'triage', 'attempt', 'review', 'wait', 'block', 'archive', 'reopen'}:
        raise ValueError('invalid task action')
    store = Store(db_path(path))
    def reduce(old_event, p):
        tasks, _ = fold(timeline(path, epoch), now)
        old = tasks.get(p['code'])
        if (old['research_version'] if old else 0) != p['expected_research_version']:
            raise ValueError('research version conflict')
        if old and reviewed < instant(old['last_attempt_at']):
            raise ValueError('task time cannot move backwards')
        t = dict(old) if old else _seed({'code': p['code'], 'recorded_at': now.isoformat(), 'reviewed_at': p['reviewed_at']}, 'active')
        state = t.get('effective_status', t['status'])
        if action == 'start' and old:
            raise ValueError('task already exists; discovery cannot reopen it')
        held = {r.get('code') or r.get('ticker') or r.get('ts_code') for r in data.get('holdings', data.get('confirmed_holdings', []))}
        if action == 'start' and sum(t['overdue'] and t['code'] not in held for t in tasks.values()) >= 20:
            raise ValueError('overdue backlog uses new-start budget; research results may still be saved')
        if not old and action != 'start':
            raise ValueError('task not found')
        if action == 'triage' and state != 'unclassified':
            raise ValueError('triage requires unclassified task; use review for forced disposition')
        if action == 'attempt' and state not in LIVE - {'review_required'}:
            raise ValueError('mandatory disposition review or explicit reopen required')
        if action == 'review' and state not in LIVE:
            raise ValueError('review requires live task')
        if action == 'reopen' and state not in PARKED | {'completed'}:
            raise ValueError('explicit reopen requires parked/completed task')
        hashes = _evidence(path, p.get('sources', []), reviewed)
        new_hashes = hashes - set(t['seen_hashes'])
        if action in {'start', 'triage', 'reopen'} or (action == 'review' and p['decision'] == 'continue'):
            if not isinstance(p.get('open_gaps'), list) or not p['open_gaps'] or not all(isinstance(g, str) and g.strip() for g in p['open_gaps']) or len(set(p['open_gaps'])) != len(p['open_gaps']) or not p.get('next_action'):
                raise ValueError('concrete open_gaps and next_action required')
            due = instant(p['due'])
            if action in {'start', 'reopen'} and due <= now:
                raise ValueError('future due required')
            if t['original_due'] is None:
                t.update(original_due=p['due'], due=p['due'])
            elif p['due'] != t['due']:
                if not p.get('extension_reason') or not p.get('continuation_basis') or due <= now:
                    raise ValueError('due cannot roll forward without extension_reason and continuation_basis')
                t['extensions'] = t['extensions'] + [{'from': t['due'], 'to': p['due'], 'reason': p['extension_reason'],
                    'at': now.isoformat(), 'request_id': p['request_id']}]
                t['due'] = p['due']
            if action == 'reopen':
                trigger = p.get('trigger_key')
                reopen_hashes = hashes - set(t.get('parked_evidence_hashes', t['seen_hashes']))
                if not reopen_hashes or (trigger and trigger != t.get('trigger_key')):
                    raise ValueError('reopen requires new verifiable evidence and matching original trigger if supplied')
                t['generation'] += 1
                t['reopen_hashes'] = t['reopen_hashes'] + sorted(reopen_hashes)
            if action == 'review' and not p.get('continuation_basis'):
                raise ValueError('continue requires concrete investment continuation_basis')
            t.update(status='active', open_gaps=p['open_gaps'], next_action=p['next_action'])
            if action != 'triage':
                t['no_progress'] = 0
        if action == 'attempt':
            resolved = p.get('resolved_gaps', [])
            if any(g not in t['open_gaps'] for g in resolved) or (resolved and (not hashes or not p.get('resolution_basis'))):
                raise ValueError('gap resolution requires known gap, evidence and resolution_basis')
            t['open_gaps'] = [g for g in t['open_gaps'] if g not in resolved]
            t['resolved_gaps'] = t['resolved_gaps'] + resolved
            progress = bool(new_hashes or resolved)
            t['no_progress'] = 0 if progress else t['no_progress'] + 1
            if t['no_progress'] >= 2:
                t.update(status='review_required', disposition_review_event_id=p['request_id'])
            if 'due' in p and p['due'] != t['due']:
                raise ValueError('attempt cannot change due')
        target = {'wait': 'waiting', 'block': 'blocked', 'archive': 'archived'}.get(action)
        if action == 'review' and p['decision'] != 'continue':
            target = {'wait': 'waiting', 'block': 'blocked', 'archive': 'archived'}.get(p['decision'])
            if target is None:
                raise ValueError('review decision must be continue/wait/block/archive')
        if target:
            if state not in LIVE:
                raise ValueError('disposition requires live task; parked tasks need explicit reopen')
            if not p.get('next_action') or not p.get('disposition_basis'):
                raise ValueError('disposition_basis and next_action required')
            t.update(status=target, disposition_basis=p['disposition_basis'], next_action=p['next_action'],
                     parked_evidence_hashes=sorted(set(t['seen_hashes']) | hashes))
            if target in {'waiting', 'blocked'}:
                if not p.get('trigger_key') or not p.get('trigger_condition') or instant(p['check_at']) <= now:
                    raise ValueError('wait/block require trigger and future latest check_at')
                t.update(trigger_key=p['trigger_key'], trigger_condition=p['trigger_condition'], check_at=p['check_at'])
        if action in {'attempt', 'review'}:
            t['attempts'] += 1
        t.update(last_attempt_at=p['reviewed_at'], seen_hashes=sorted(set(t['seen_hashes']) | hashes),
                 owner='investment', last_reason=p['reason'])
        for derived in ('effective_status', 'overdue', 'research_version', 'admission_state', 'research_event_id'):
            t.pop(derived, None)
        if path.read_bytes() != raw:
            raise ValueError('scope changed during task validation')
        return {**p, 'task': t, 'recorded_at': now.isoformat(), 'schema_version': 1}
    try:
        return store.apply('research-task:' + epoch + ':' + event['code'], event['expected_version'],
                           event['request_id'], event, event['reason'], reduce)
    finally:
        store.close()


def queue(scope, records=(), limit=10):
    snap = scope['research_pool']['tasks']
    by = {r['code']: r for r in records}
    rows = [t for t in snap['rows'] if t['effective_status'] in LIVE and t['code'] not in scope['holdings']]
    rows.sort(key=lambda t: (not t['overdue'], t['last_attempt_at'], t.get('due') or t['created_at'], t['code']))
    return [{**by.get(t['code'], {'code': t['code'], 'channels': [], 'research_gap': 'outside_panel'}),
             'task_status': t['effective_status'], 'review_event_id': t['research_event_id'],
             'expected_task_version': t['version'],
             'expected_version': t['research_version'], 'original_due': t['original_due'], 'due': t['due'],
             'last_attempt_at': t['last_attempt_at'], 'open_gaps': t['open_gaps'],
             'review_reason': t.get('legacy_gap') or t.get('next_action') or '未分类待 investment 分流',
             'next_action': t.get('next_action'), 'no_progress': t['no_progress'],
             'research_only': True, 'owner': 'investment'} for t in rows[:limit]]


def render(snap):
    a = snap['accounting']
    lines = ['**研究任务出入账｜不等于证券退出**', f"区间 {snap['since']} — {snap['asof']}；责任 investment。",
             '|期初活跃待办|新启动|重开|真实完成|转等待|转受阻|搁置|期末活跃待办|逾期|',
             '|---|---|---|---|---|---|---|---|---|',
             '|' + '|'.join(str(a[k]) for k in ('opening_active','started','reopened','completed','waiting','blocked','archived','closing_active','overdue')) + '|',
             f"对账：期初 + 新启动 + 重开 − 真实完成 − 转等待 − 转受阻 − 搁置 = 期末（{a['reconciled']}）。",
             '当前状态：' + '；'.join(f'{LABELS[k]} {v}' for k,v in snap['counts'].items()),
             '等待/受阻/归档留存历史：' + json.dumps(snap['parked_history'], ensure_ascii=False) + f"；总未解决 {snap['unresolved_total']}（含搁置，不因改名减少）。",
             '任务归档不是公司差、证券退出或删除；持仓和曾准入 episode 行情/风险监控继续。',
             '|代码|任务处置|原 due / 当前 due|无进展次数|缺口 / 下一动作|', '|---|---|---|---|---|']
    for t in snap['rows']:
        if t['effective_status'] == 'completed':
            continue
        text = '; '.join(t['open_gaps']) or t.get('legacy_gap') or '未分类待分流'
        text = (text + ' / ' + (t.get('next_action') or 'investment 明确分流动作')).replace('|','／').replace('\n',' ')
        lines.append(f"|{t['code']}|{LABELS[t['effective_status']]}|{t['original_due']} / {t['due']}|{t['no_progress']}|{text}|")
    return '\n'.join(lines) + '\n'


def inventory(path, epoch, now=None):
    now = now or datetime.now(timezone.utc)
    entries = timeline(path, epoch)
    tasks, _ = fold(entries, now)
    rows = []
    for code, t in tasks.items():
        history = [r for kind, r in entries if kind == 'research' and r['code'] == code]
        unknown = [k for k in ('open_gaps','next_action','due') if not t.get(k)]
        rows.append({**t, 'age_days': (now - instant(t['created_at'])).days,
            'research_event_count': len(history), 'repeat_count': t['no_progress'],
            'unknown_fields': unknown,
            'suggested_action': 'investment 强制处置复核：具体继续依据 / 等待触发 / 受阻 / 搁置；不得按年龄拒绝' if t['status'] == 'review_required' else
                                'investment 补齐任务分类、缺口、下一动作、due/触发' if t['status'] == 'unclassified' else
                                '按现有任务状态处理；不代替公司判断',
            'history': [{'request_id': r['request_id'], 'version': r['version'], 'reason': r['reason'],
                         'due': r.get('next_review'), 'reviewed_at': r['reviewed_at'], 'sources': r.get('sources', [])} for r in history]})
    return {'mode': 'readonly_triage_proposal_not_company_disposition', 'scope_epoch': epoch, 'asof': now.isoformat(),
            'rows': rows, 'company_dispositions_written': 0}


def render_inventory(value):
    lines = ['**旧研究任务只读分流清单｜不是实际处置**',
             f"快照 {value['asof']}；epoch {value['scope_epoch']}；共 {len(value['rows'])} 项。",
             '未知字段明确待 investment 分流，不按年龄/缺字段自动拒绝或归档。重复次数只统计同证据 hash 的无实质进展，不代做语义研究。',
             '|代码|证券/任务状态|年龄天|原 due / 当前 due|连续无进展 / 累计尝试|缺口|建议下一动作|',
             '|---|---|---|---|---|---|---|']
    for t in value['rows']:
        gap = ('; '.join(t['open_gaps']) or t.get('legacy_gap') or '未分类待分流').replace('|','／').replace('\n',' ')
        lines.append(f"|{t['code']}|{t['admission_state']} / {LABELS[t['status']]}|{t['age_days']}|{t['original_due']} / {t['due']}|{t['repeat_count']} / {t['attempts']}|{gap}|{t['suggested_action']}|")
    lines += ['全部旧原文、理由、due、事件版本见配套 inventory.json.history；本清单零生产处置写入。']
    return '\n'.join(lines) + '\n'
