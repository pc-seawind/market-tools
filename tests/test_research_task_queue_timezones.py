"""Read-only synthetic queue projections: compare instants, preserve raw history."""
from copy import deepcopy

import pytest

from mt1 import research_tasks as tasks, research_pool as pool


def row(code, last='2026-11-01T12:00:00+00:00', due='2026-11-10T00:00:00+00:00',
        created='2026-10-30T00:00:00+00:00', overdue=False, status='active'):
    return {'code': code, 'status': status, 'effective_status': status, 'overdue': overdue,
            'last_attempt_at': last, 'due': due, 'original_due': due, 'created_at': created,
            'research_event_id': 'synthetic-'+code, 'version': 1, 'research_version': 2,
            'open_gaps': ['cash_bridge'], 'next_action': '核对原文现金桥', 'no_progress': 0}


def scope(rows, held=()):
    return {'holdings': {code: {'code': code} for code in held},
            'research_pool': {'rows': [], 'tasks': {'rows': rows}}}


def test_mixed_timezone_attempts_cross_utc_day_in_review_repro_order():
    first = row('300725.SZ', last='2026-11-03T07:26:50.344281+08:00')
    second = row('301132.SZ', last='2026-11-03T05:26:15.353862+00:00')
    value = scope([second, first]); before = deepcopy(value)
    result = tasks.queue(value)
    assert [r['code'] for r in result] == ['300725.SZ', '301132.SZ']
    assert result[0]['last_attempt_at'] == first['last_attempt_at']
    assert result[1]['last_attempt_at'] == second['last_attempt_at']
    assert value == before  # Sorting never normalizes or rewrites stored ISO text.


def test_same_attempt_instant_in_different_iso_forms_uses_due_tiebreak():
    late_due = row('a.SZ', last='2026-11-02T23:30:00+00:00', due='2026-11-12T00:00:00+00:00')
    early_due = row('z.SZ', last='2026-11-03T07:30:00+08:00', due='2026-11-11T00:00:00+00:00')
    assert [r['code'] for r in tasks.queue(scope([late_due, early_due]))] == ['z.SZ', 'a.SZ']


def test_equal_attempt_and_due_instants_use_code_not_iso_spelling():
    a = row('a.SZ', last='2026-11-03T07:30:00+08:00', due='2026-11-10T08:00:00+08:00')
    z = row('z.SZ', last='2026-11-02T23:30:00+00:00', due='2026-11-10T00:00:00+00:00')
    for rows in ([z, a], [a, z]):
        assert [r['code'] for r in tasks.queue(scope(rows))] == ['a.SZ', 'z.SZ']


@pytest.mark.parametrize('field', ['due', 'created_at'])
def test_secondary_time_key_compares_instants_including_existing_created_fallback(field):
    a, z = row('a.SZ'), row('z.SZ')
    a[field] = '2026-11-10T07:00:00+08:00'  # Nov 9 23:00 UTC, earlier despite lexical 07 > 02.
    z[field] = '2026-11-10T02:00:00+00:00'
    if field == 'created_at':
        a['due'] = z['due'] = None
        a['last_attempt_at'] = z['last_attempt_at'] = '2026-11-11T00:00:00+00:00'
    assert [r['code'] for r in tasks.queue(scope([z, a]))] == ['a.SZ', 'z.SZ']


def test_overdue_priority_stays_ahead_of_attempt_and_due_order():
    old = row('old-not-overdue.SZ', last='2026-10-31T00:00:00+00:00')
    overdue = row('recent-overdue.SZ', last='2026-11-03T07:00:00+08:00',
                  due='2026-11-02T00:00:00+00:00', overdue=True)
    assert tasks.queue(scope([old, overdue]), limit=1)[0]['code'] == overdue['code']


def test_membership_limit_and_record_binding_are_unchanged():
    a = row('a.SZ', last='2026-11-03T07:26:50+08:00')
    z = row('z.SZ', last='2026-11-03T05:26:15+00:00', status='review_required')
    unknown = row('unknown.SZ', last='2026-11-03T06:26:15+00:00', status='unclassified')
    excluded = [row(s+'.SZ', status=s) for s in ('waiting', 'blocked', 'archived', 'completed')]
    value = scope([z, unknown, a, row('held.SZ'), *excluded], held=['held.SZ'])
    records = [{'code': 'a.SZ', 'channels': ['SYNTHETIC'], 'bound_data': 'keep'}]
    before = deepcopy(value)
    assert {r['code'] for r in tasks.queue(value)} == {'a.SZ', 'z.SZ', 'unknown.SZ'}
    result = tasks.queue(value, records, limit=1)
    assert result[0]['code'] == 'a.SZ' and result[0]['bound_data'] == 'keep'
    assert result[0]['expected_task_version'] == 1 and result[0]['expected_version'] == 2
    assert value == before


@pytest.mark.parametrize('overdue,old_budget,new_budget', [(0, 10, 10), (10, 10, 10),
                                                          (15, 15, 5), (25, 20, 0)])
def test_timezone_sort_does_not_change_old_first_or_total_batch_budgets(overdue, old_budget, new_budget):
    rows = [row(f'old-{n:02}.SZ', last=('2026-11-03T07:00:00+08:00' if n % 2 else
                                      '2026-11-03T01:00:00+00:00'), overdue=n < overdue,
                due=('2026-11-02T00:00:00+00:00' if n < overdue else '2026-11-10T00:00:00+00:00'))
            for n in range(30)]
    value = scope(rows)
    new = [{'code': f'new-{n:02}.SZ', 'channel_risk': {}} for n in range(30)]
    outside, batch, _ = pool.research_queues(new, new, value)
    reviews = pool.review_queue(value)
    assert len(reviews) == old_budget and len(batch) == new_budget
    assert len(outside) == 30 and len(reviews) + len(batch) == 20
    assert {r['code'] for r in reviews} <= {r['code'] for r in rows}
    assert not {r['code'] for r in reviews} & {r['code'] for r in batch}
    assert len(pool.review_queue(value, limit=3)) == 3
