"""Takeover-only regressions: real CLI, parked boundaries, fairness and report windows.

All companies/scopes are synthetic. No live API, production events or remote work.
"""
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from mt1 import research_tasks as tasks, research_pool as pool
from mt1.scope import load
from test_research_tasks import case, task, evidence, get


@pytest.mark.parametrize('gaps', ['cash', [], ['cash', 'cash'], [' ']])
def test_open_gaps_is_a_concrete_json_list_at_input_boundary(case, gaps):
    with pytest.raises(ValueError, match='concrete open_gaps'):
        task(case, open_gaps=gaps)
    assert tasks.timeline(case.scope, case.e['scope_epoch']) == []


def test_real_task_cli_exact_retry_and_research_version_conflict(case):
    p = {k: case.e[k] for k in ('scope_epoch', 'scope_sha256', 'domain', 'reviewer', 'code', 'reviewed_at')}
    p.update(action='start', request_id='synthetic-cli-start', expected_version=0,
             expected_research_version=0, reason='SYNTHETIC 真正启动，不是扫描',
             open_gaps=['cash_bridge'], next_action='核对原文经营现金流', due=case.e['next_review'])
    path = case.root/'task.json'; path.write_text(json.dumps(p))
    cmd = [sys.executable, 'mt1.py', '--scope', str(case.scope), 'research-task', '--input', str(path)]
    first = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=True)
    second = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=True)
    assert json.loads(first.stdout) == json.loads(second.stdout)
    assert tasks.snapshot(case.scope, case.e['scope_epoch'])['rows'][0]['version'] == 1
    assert len(tasks.timeline(case.scope, case.e['scope_epoch'])) == 1
    path.write_text(json.dumps({**p, 'action': 'attempt', 'expected_version': 1,
                               'expected_research_version': 3, 'request_id': 'stale-research'}))
    conflict = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    assert conflict.returncode != 0 and 'research version conflict' in conflict.stderr
    assert case.scope.read_bytes() == case.before


def test_wait_keeps_original_check_after_pending_save_and_early_trigger_is_bound(case):
    task(case)
    due = get(case)['due']; check = case.now + timedelta(days=2)
    task(case, 'wait', disposition_basis='等待新版财报', next_action='核对新版披露',
         trigger_key='next-filing', trigger_condition='发布新版财报', check_at=check.isoformat())
    case.put(state='pending', research_outcome='data_blocked', gap_or_exit_basis='原文仍受阻',
             next_review=(case.now + timedelta(days=20)).isoformat())
    assert get(case)['status'] == 'waiting' and get(case)['check_at'] == check.isoformat()
    assert get(case)['due'] == due
    assert pool.review_queue(load(case.scope)) == []
    src = evidence(case, 'SYNTHETIC new filing with matching trigger')
    with pytest.raises(ValueError, match='matching original trigger'):
        task(case, 'reopen', sources=[src], trigger_key='other-event')
    task(case, 'reopen', sources=[src], trigger_key='next-filing')
    assert get(case)['status'] == 'active' and get(case)['generation'] == 2


def test_latest_check_and_fresh_hash_do_not_bypass_forced_review(case):
    task(case)
    task(case, 'block', disposition_basis='来源不可用', next_action='检查来源恢复',
         trigger_key='source-recovery', trigger_condition='来源可用',
         check_at=(case.now + timedelta(seconds=1)).isoformat())
    case.now += timedelta(seconds=2)
    # Saving real evidence remains allowed; it cannot silently satisfy the required review.
    case.put(state='pending', research_outcome='partially_researched', gap_or_exit_basis='仍缺估值',
             sources=[evidence(case, 'SYNTHETIC changed evidence after latest check')], reviewed_at=case.now.isoformat())
    row = get(case)
    assert row['status'] == 'review_required' and row['no_progress'] == 0
    inv = tasks.inventory(case.scope, case.e['scope_epoch'], case.now)
    assert '强制处置复核' in inv['rows'][0]['suggested_action']
    with pytest.raises(ValueError, match='mandatory disposition'):
        task(case, 'attempt')


def test_old_work_rotates_after_actual_attempt_without_rolling_due(case):
    for n in range(25):
        task(case, code=f'old-{n:02}', due=(case.now + timedelta(seconds=1)).isoformat())
    case.now += timedelta(seconds=2)
    def scoped():
        scope = load(case.scope)
        scope['research_pool']['tasks'] = tasks.snapshot(case.scope, case.e['scope_epoch'], case.now)
        return scope
    before = pool.review_queue(scoped())
    assert len(before) == 20
    original = {t['code']: t['due'] for t in tasks.snapshot(case.scope, case.e['scope_epoch'], case.now)['rows']}
    for row in before:
        task(case, 'attempt', code=row['code'])
    after = pool.review_queue(scoped())
    assert {r['code'] for r in after[:5]} == {f'old-{n:02}' for n in range(20, 25)}
    assert {t['code']: t['due'] for t in tasks.snapshot(case.scope, case.e['scope_epoch'], case.now)['rows']} == original


def test_insufficient_evidence_does_not_reject_but_evidenced_rejection_completes(case):
    task(case)
    for _ in range(2):
        task(case, 'attempt')
    assert get(case)['status'] == 'review_required'
    assert pool.read_events(case.scope, case.e['scope_epoch']) == []
    with pytest.raises(ValueError, match='completed rejection requires researched evidence'):
        case.put(state='rejected', research_outcome='completed_not_qualified', gap_or_exit_basis='不合格依据', sources=[])
    case.put(state='rejected', research_outcome='completed_not_qualified', gap_or_exit_basis='SYNTHETIC 原文充分证伪')
    row = get(case)
    assert row['status'] == 'completed' and row['completion_state'] == 'rejected'
    assert tasks.snapshot(case.scope, case.e['scope_epoch'], case.now)['accounting']['completed'] == 1


@pytest.mark.parametrize('phase', ['morning', 'evening', 'saturday', 'sunday'])
def test_four_phase_text_and_json_share_accounting_window(case, monkeypatch, phase):
    task(case)
    monkeypatch.setenv('MT1_TRACKING_SCOPE', str(case.scope))
    from mt1.pipeline import run
    result = run(phase, case.root/'pipeline', case.root/'investment', now=case.now,
                 fixture={'calendars': {'CN': {}}}, run_id='takeover-'+phase)
    text = Path(result['report_path']).read_text()
    snap = result['research_pool']['tasks']
    since = datetime.fromisoformat(snap['since'])
    local = case.now.astimezone(ZoneInfo('Asia/Shanghai')).replace(hour=0, minute=0, second=0, microsecond=0)
    expected = local - timedelta(days=local.weekday()) if phase in {'saturday', 'sunday'} else local
    assert since == expected and '区间 '+snap['since']+' — '+snap['asof'] in text
    assert tasks.render(snap) in text and snap['accounting']['reconciled']
    assert snap['accounting']['closing_active'] == 1
    saved = json.loads((case.root/'pipeline'/'runs'/('takeover-'+phase)/'report.json').read_text())
    assert saved['research_pool']['tasks'] == snap


def test_archived_old_task_never_rejoins_scan_and_held_monitor_remains(case):
    case.put()
    task(case, 'reopen', sources=[evidence(case, 'SYNTHETIC supplement scope')])
    task(case, 'archive', disposition_basis='补研优先级不足，不退出证券', next_action='新披露显式重开')
    scope = load(case.scope)
    rows = [{'code': case.e['code'], 'channel_risk': {}}, {'code': 'brand-new', 'channel_risk': {}}]
    assert [r['code'] for r in pool.research_queues(rows, rows, scope)[1]] == ['brand-new']
    assert len(scope['holdings']) == 9 and case.e['code'] in scope['candidates']
    assert any(r['code'] == case.e['code'] for r in scope['research_pool']['monitor_rows'])
    assert scope['research_pool']['episodes'][0]['exit'] is None
    assert tasks.snapshot(case.scope, case.e['scope_epoch'], case.now)['counts']['archived'] == 1
