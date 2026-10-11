"""Post-reset research lifecycle. No holdings, trades, external watchlist or BUY writes.

SQLite serializes event CAS. Scope is a read-time projection, never a rewrite of
user-owned JSON. An epoch change therefore hides old events even across crashes.
"""
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from .store import Store, digest

ACTIVE = {'watch', 'recommended'}
STATES = ACTIVE | {'pending', 'rejected', 'exited'}
LIMITS = {'watch': 50, 'recommended': 10, 'pending': 100}


def instant(s):
    d = datetime.fromisoformat(s.replace('Z', '+00:00'))
    if d.tzinfo is None:
        raise ValueError('timezone required')
    return d


def db_path(scope_path):
    return Path(str(scope_path) + '.research.sqlite3')


def read_events(scope_path, epoch):
    p = db_path(scope_path)
    if not p.exists():
        return []
    con = sqlite3.connect('file:' + str(p.resolve()) + '?mode=ro', uri=True)
    try:
        rows = [json.loads(r[0]) for r in con.execute('SELECT after_json FROM events ORDER BY seq')]
        return [r for r in rows if r['scope_epoch'] == epoch]
    finally:
        con.close()


def snapshot(scope_path, epoch, now=None):
    now = now or datetime.now(timezone.utc)
    events = read_events(scope_path, epoch)
    latest = {r['code']: r for r in events}
    rows = []
    for r in latest.values():
        expired = r['state'] in ACTIVE and instant(r['next_review']) <= now
        rows.append({**r, 'effective_state': 'pending' if expired else r['state'],
                     'review_overdue': expired, 'method_status': 'shadow', 'final_buy': False})
    monitor = [r for r in rows if r['state'] in ACTIVE]
    exits = sorted((r for r in rows if r['state'] == 'exited' and now - instant(r['recorded_at']) <= timedelta(days=93)), key=lambda r: r['recorded_at'], reverse=True)
    monitor += exits[:100]
    return {'scope_epoch': epoch, 'asof': now.isoformat(), 'rows': rows, 'events': events,
            'monitor_rows': monitor, 'exit_monitor_omitted': max(0, len(exits)-100),
            'exit_monitor_policy': '93 calendar days, at most 100 latest exits; history retained',
            'counts': {s: sum(r['effective_state'] == s for r in rows) for s in STATES}}


def project(scope, path, now=None):
    snap = snapshot(path, scope['epoch'], now)
    # Events override administrative candidate rows for the same code, including
    # exits and overdue reviews. Nothing ever changes scope['holdings'].
    for r in snap['rows']:
        code = r['code']
        scope['candidates'].pop(code, None)
        scope['recommendations'].pop(code, None)
        if r['effective_state'] in ACTIVE:
            key = 'recommendations' if r['effective_state'] == 'recommended' else 'candidates'
            scope[key][code] = {**r, 'admitted_at': r['episode_admitted_at'],
                                'research_event_id': r['request_id']}
    scope['research_pool'] = snap
    return scope


def apply(scope_path, event, now=None):
    """Investment-supplied evidence only; discovery cannot call this implicitly."""
    now = now or datetime.now(timezone.utc)
    path = Path(scope_path)
    raw = path.read_bytes()
    scope_hash = hashlib.sha256(raw).hexdigest()
    data = json.loads(raw)
    epoch = data.get('epoch') or data['scope_epoch']
    if event['scope_epoch'] != epoch or event['scope_sha256'] != scope_hash:
        raise ValueError('scope CAS/epoch conflict')
    if event.get('domain') != 'investment' or not event.get('reviewer'):
        raise ValueError('investment research reviewer required')
    code = event['code']
    if not isinstance(code, str) or not code or ':' in code:
        raise ValueError('invalid code')
    if event.get('state') not in STATES:
        raise ValueError('invalid state')
    if not event.get('reason') or not event.get('request_id'):
        raise ValueError('reason and request_id required')
    store = Store(db_path(path))
    def reduce(old, patch):
        reviewed = instant(patch['reviewed_at'])
        if not instant(data['reset_at']) <= reviewed <= now or now - reviewed > timedelta(days=7):
            raise ValueError('stale/future/pre-reset research')
        target = patch['state']
        held = {r.get('code') or r.get('ticker') or r.get('ts_code') for r in data.get('holdings', data.get('confirmed_holdings', []))}
        if target in ACTIVE and code in held:
            raise ValueError('holdings review is separate from outside research admission')
        if old and reviewed < instant(old['reviewed_at']):
            raise ValueError('research time cannot move backwards')
        if target == 'rejected' and old and old['state'] in ACTIVE:
            raise ValueError('admitted thesis failure requires explicit exited event')
        if target == 'exited' and (not old or old['state'] not in ACTIVE):
            raise ValueError('exit requires previously admitted research')
        # A new event/version is mandatory for re-entry; reuse of original
        # admission evidence is not accepted as a fresh research episode.
        if old and old['state'] in {'exited', 'rejected'} and target in ACTIVE:
            if reviewed <= instant(old['reviewed_at']):
                raise ValueError('re-entry requires new research')
        if target in ACTIVE:
            for field in ('business_logic', 'risk', 'next_review', 'sources', 'milestones'):
                if not patch.get(field):
                    raise ValueError('admission missing ' + field)
            due = instant(patch['next_review'])
            if not now < due <= reviewed + timedelta(days=93):
                raise ValueError('invalid next review')
            for milestone in patch['milestones']:
                if not milestone.get('description') or not reviewed + timedelta(days=28) <= instant(milestone['at']) <= reviewed + timedelta(days=93):
                    raise ValueError('1-3 month milestone required')
            for src in patch['sources']:
                if not src['url'].startswith(('https://', 'http://')):
                    raise ValueError('verifiable source URL required')
                if not instant(src['published_at']) <= instant(src['fetched_at']) <= reviewed:
                    raise ValueError('source time invalid')
                if hashlib.sha256(Path(src['path']).read_bytes()).hexdigest() != src['sha256']:
                    raise ValueError('source hash mismatch')
            if target == 'recommended':
                for field in ('valuation', 'risk_signoff', 'valuation_signoff'):
                    if not patch.get(field):
                        raise ValueError('recommendation missing ' + field)
                for key in ('risk_signoff', 'valuation_signoff'):
                    sig = patch[key]
                    if not sig.get('reviewer') or sig.get('status') != 'pass' or not reviewed <= instant(sig['at']) <= now:
                        raise ValueError('invalid signoff')
        else:
            if not patch.get('gap_or_exit_basis'):
                raise ValueError('pending/reject/exit basis required; unknown is not rejection')
            if target == 'rejected' and patch.get('research_outcome') != 'completed_not_qualified':
                raise ValueError('unknown must remain pending')
            if target == 'pending' and patch.get('research_outcome') not in ('unresearched', 'data_blocked', 'partially_researched'):
                raise ValueError('explicit pending coverage required')
        if target == 'rejected':
            if not all(patch.get(k) for k in ('sources', 'business_logic', 'risk')):
                raise ValueError('completed rejection requires researched evidence and reasoning')
            for src in patch['sources']:
                if not src['url'].startswith(('https://', 'http://')) or not instant(src['published_at']) <= instant(src['fetched_at']) <= reviewed:
                    raise ValueError('rejection source invalid')
        # Caps are evaluated while Store holds BEGIN IMMEDIATE.
        current = {r['code']: r for r in read_events(path, epoch)}
        if target in LIMITS and sum(r['state'] == target and c != code and (target not in ACTIVE or instant(r['next_review']) > now) for c, r in current.items()) >= LIMITS[target]:
            raise ValueError('pool capacity reached')
        if path.read_bytes() != raw:
            raise ValueError('scope changed during validation')
        episode_new = target in ACTIVE and (not old or not old.get('episode_admitted_at') or old['state'] in {'exited', 'rejected'})
        original = ({'reason': patch['reason'], 'milestones': patch['milestones'], 'sources': patch['sources'],
                     'next_review': patch['next_review']} if episode_new else (old or {}).get('original'))
        # Freeze evidence by content address before event commit. A crash can
        # leave an unused blob, never a committed event with mutable evidence.
        for src in patch.get('sources', []):
            source_raw = Path(src['path']).read_bytes()
            if hashlib.sha256(source_raw).hexdigest() != src['sha256']:
                raise ValueError('source changed')
            dest = Path(str(path) + '.research-sources') / src['sha256']
            dest.parent.mkdir(exist_ok=True)
            if dest.exists():
                if dest.read_bytes() != source_raw:
                    raise ValueError('source archive conflict')
            else:
                with dest.open('xb') as f:
                    f.write(source_raw)
                    f.flush()
                    os.fsync(f.fileno())
        if path.read_bytes() != raw:
            raise ValueError('scope changed while archiving evidence')
        prior = (old or {}).get('state')
        change = ('维持' if prior == target else '退出' if target == 'exited' else '降级' if prior == 'recommended' and target == 'watch' else '升级' if prior == 'watch' and target == 'recommended' else '重新准入' if prior in {'exited', 'rejected'} and target in ACTIVE else '新增' if target in ACTIVE else '待核验' if target == 'pending' else '研究不合格')
        return {**patch, 'schema_version': 1, 'change_type': change, 'original': original,
                'source_archive': [{'sha256': s['sha256'], 'path': str(Path(str(path) + '.research-sources') / s['sha256'])} for s in patch.get('sources', [])],
                'episode_admitted_at': now.isoformat() if episode_new else (old or {}).get('episode_admitted_at'),
                'previous_state': (old or {}).get('state'), 'recorded_at': now.isoformat(),
                'method_status': 'shadow', 'final_buy': False}
    try:
        return store.apply('research:' + epoch + ':' + code, event['expected_version'], event['request_id'],
                           event, event['reason'], reduce)
    finally:
        store.close()


def research_queues(ranked, records, scope, batch_size=10):
    """Scope is a formal-plan boundary, NEVER a discovery/research prerequisite."""
    held = set(scope['holdings']) if scope else set()
    outside = [r for r in ranked if r['code'] not in held and
               not any(v['status'] == 'reject' for v in r['channel_risk'].values())]
    by = {r['code']: r for r in records}
    holdings = [by.get(c, {'code': c, 'channels': [], 'research_gap': 'outside_panel'}) for c in sorted(held)]
    states = {r['code']: r for r in (scope or {}).get('research_pool', {}).get('rows', [])}
    eligible = [r for r in outside if r['code'] not in states or states[r['code']]['effective_state'] == 'pending']
    # New/unattempted items first; active recommendations are not re-discovered
    # as new. Exits/rejections require explicit new research rather than auto-reentry.
    eligible.sort(key=lambda r: r['code'] in states)
    return outside, eligible[:batch_size], holdings


def report(scope, coverage=None):
    snap = scope.get('research_pool', {'rows': [], 'events': [], 'counts': {}}) if scope else {'rows': [], 'events': [], 'counts': {}}
    coverage_text = '持仓外覆盖未附：故障，不能声称完成机会研究'
    if coverage:
        coverage_text = (f"数据日 {coverage.get('asof')}；扫描 {coverage.get('universe')}；发现 {coverage.get('discovered')}；"
                         f"持仓外排队 {coverage.get('outside_backlog')}；本批 {coverage.get('outside_batch')}；持仓复核 {coverage.get('holdings_review', coverage.get('holding_review'))}。")
        completed = coverage.get('completed_company_research', coverage.get('company_research_completed'))
        coverage_text += f' 本轮公司研究完成：{completed if completed is not None else "未签审，不能从扫描推断"}；新增入池：{coverage.get("new_admissions", "以研究事件为准")}。'
        if coverage.get('coverage_fault'): coverage_text += ' 持仓外覆盖故障。'
        if str(coverage.get('mode', '')).startswith('offline'): coverage_text += ' 本次为离线手工重放，不是自然执行。'
    lines = ['**研究池与真实持仓分离｜非交易建议**',
             '发现、排队、原文研究、待核验、正式入池分别计数；0 新推荐不表示全部研究不合格。',
             '扫描/研究范围与日期：' + coverage_text,
             '已记录研究事件分层：' + json.dumps(snap['counts'], ensure_ascii=False) + '；pending 须逐项区分 unresearched / data_blocked / partially_researched；rejected 仅代表有证据的 completed_not_qualified。',
             '**实际持仓复核**', '|代码|状态|', '|---|---|']
    for c in sorted(scope['holdings'] if scope else []):
        lines.append(f'|{c}|用户持仓不变；公司复核及收益证据待独立核验|')
    lines += ['**持仓外机会与重点研究推荐**', '|代码|池状态|理由/缺口|下次复核|', '|---|---|---|---|']
    for r in snap['rows']:
        lines.append(f"|{r['code']}|{r['effective_state']}|{r['reason']}；{r.get('research_outcome', '已核验准入材料' if r['state'] in ACTIVE else '见退出证据')}|{r.get('next_review', '待核验')}|")
    if not snap['rows']:
        lines.append('|无正式入池|0|尚无新研究事件；不是研究后全部淘汰|待 investment 研究|')
    lines += ['**新增/升级/降级/退出/维持变更与退出后验证**', '|代码|原状态→新状态|版本/原理由|时间|', '|---|---|---|---|']
    for r in snap['events'][-100:]:
        lines.append(f"|{r['code']}|{r['change_type']} {r['previous_state']}→{r['state']}|v{r['version']} {r['reason']}|{r['recorded_at']}|")
    if not snap['events']:
        lines.append('|无|无变更|未发生已签审研究事件|本次读取|')
    lines += ['退出记录保留，收益/原判断兑现待后续可核验行情，不填造收益；过期研究回到待核验，不自动续期。',
              '方法仍为 shadow；重点研究推荐不是 BUY，正式计划仍需独立签审。']
    return '\n'.join(lines) + '\n'


def replay(source, out, scope_path):
    """Queue-only offline replay of an immutable cohort; no price/feature recalc."""
    import gzip
    from .scope import load
    root, dest = Path(source), Path(out)
    dest.mkdir(parents=True, exist_ok=False)
    raw_summary = (root / 'summary.json').read_bytes()
    summary = json.loads(raw_summary)
    raw_funnel = (root / 'funnel.json.gz').read_bytes()
    expected = summary.get('frozen_hashes', {}).get('funnel.json.gz')
    if not expected or hashlib.sha256(raw_funnel).hexdigest() != expected:
        raise ValueError('frozen funnel hash mismatch')
    records = json.loads(gzip.decompress(raw_funnel))
    cohort_raw = (root / 'forward-cohort.json').read_bytes()
    cohort = json.loads(cohort_raw)
    ranked = sorted([r for r in records if r.get('channels')], key=lambda r: r['research_rank'])
    if cohort['decision_close'] != summary['asof'] or cohort['method'] != summary['method']:
        raise ValueError('cohort date/method mismatch')
    if set(cohort['codes']) != {r['code'] for r in ranked}:
        raise ValueError('cohort mismatch')
    scope_raw = Path(scope_path).read_bytes()
    scope = load(scope_path)
    outside, batch, holdings = research_queues(ranked, records, scope)
    artifacts = {}
    for name, rows in [('research-queue.jsonl', outside), ('research-batch.jsonl', batch), ('holdings-review.jsonl', holdings)]:
        with (dest / name).open('x') as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + '\n')
        artifacts[name] = hashlib.sha256((dest / name).read_bytes()).hexdigest()
    for name, raw in [('source-summary.json', raw_summary), ('funnel.json.gz', raw_funnel),
                      ('forward-cohort.json', cohort_raw), ('scope-input.json', scope_raw)]:
        (dest / name).write_bytes(raw)
        artifacts[name] = hashlib.sha256(raw).hexdigest()
    import socket
    import subprocess
    coverage = {'execution_host': socket.gethostname(),
                'code_hashes': {n: hashlib.sha256(Path(__file__).with_name(n).read_bytes()).hexdigest() for n in ('research_pool.py','scope.py')},
                'git_head': subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
                'mode': 'offline_queue_replay_manual_verification_not_natural_execution',
                'scope_epoch': scope['epoch'], 'source': str(root.resolve()), 'asof': summary['asof'],
                'universe': len(records), 'discovered': len(ranked), 'outside_backlog': len(outside),
                'outside_batch': len(batch), 'holdings_review': len(holdings),
                'old_batch_codes': summary['research_batch_codes'],
                'new_batch_codes': [r['code'] for r in batch],
                'completed_company_research': 0, 'pending_verification': len(batch),
                'coverage_basis': 'queue replay did not perform company research; prior company work not inferred',
                'new_admissions': 0, 'coverage_fault': not bool(outside), 'artifacts': artifacts}
    (dest / 'coverage.json').write_text(json.dumps(coverage, ensure_ascii=False, indent=2))
    (dest / 'report.md').write_text(report(scope, coverage) + '\n**本轮持仓外研究交接（尚未研究）**\n' +
                                  '\n'.join(f"- {r['code']} {r.get('name', '')}：{','.join(r['channels'])}；原文、经营逻辑、节点、风险、估值与签审待补。" for r in batch) + '\n')
    return coverage
