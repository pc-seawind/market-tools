"""Cumulative pending records are independent of per-round research budgets."""
import sqlite3

import pytest
import test_research_pool_lifecycle as base
from mt1.research_pool import LIMITS, db_path, research_queues, review_queue, snapshot
from mt1.scope import load


@pytest.fixture
def case():
    c = base.ResearchPool()
    c.setUp()
    try:
        yield c
    finally:
        c.doCleanups()


def put_pending(case, **updates):
    return case.put(state='pending', research_outcome='partially_researched',
                    gap_or_exit_basis='synthetic evidence gap', **updates)


def seed_pending(case, count):
    for n in range(count):
        put_pending(case, code=f'pending-{n}', request_id=f'seed-{n}')


@pytest.mark.parametrize('count', [100, 105])
def test_pending_new_review_and_watch_transition_are_uncapped_and_idempotent(case, count):
    seed_pending(case, count)
    watch = case.put()
    before = snapshot(case.scope, case.e['scope_epoch'], case.now)
    new = dict(code='new-pending', request_id='new-pending')
    review = dict(code='pending-0', request_id='review-pending', expected_version=1)
    transition = dict(request_id='watch-to-pending', expected_version=1)
    results = [put_pending(case, **update) for update in (new, review, transition)]
    for update, result in zip((new, review, transition), results):
        assert put_pending(case, **update) == result

    after = snapshot(case.scope, case.e['scope_epoch'], case.now)
    assert after['counts']['pending'] == count + 2
    assert after['counts']['watch'] == 0
    assert len(after['events']) == count + 4
    assert after['events'][:-3] == before['events']
    assert results[1]['version'] == results[2]['version'] == 2
    assert results[2]['original'] == watch['original']
    assert results[2]['episode_id'] == watch['episode_id']
    assert results[2]['next_review'] == watch['next_review']
    assert results[2]['source_archive'] == watch['source_archive']
    assert case.scope.read_bytes() == case.before
    with pytest.raises(ValueError, match='version conflict'):
        put_pending(case, code='pending-0', request_id='stale-review', expected_version=1)
    with pytest.raises(ValueError, match='idempotency key reused'):
        put_pending(case, **{**new, 'reason': 'changed payload'})
    with sqlite3.connect(db_path(case.scope)) as con:
        for sql in ('UPDATE events SET reason="changed"', 'DELETE FROM events'):
            with pytest.raises(sqlite3.IntegrityError, match='append-only'):
                con.execute(sql)
    assert snapshot(case.scope, case.e['scope_epoch'], case.now)['events'] == after['events']


@pytest.mark.parametrize('state,limit', [('watch', 50), ('recommended', 10)])
def test_active_capacity_remains_enforced(case, state, limit):
    assert LIMITS[state] == limit
    extra = {}
    if state == 'recommended':
        sig = {'status': 'pass', 'reviewer': 'synthetic', 'at': case.now.isoformat()}
        extra = dict(valuation='synthetic scenario', risk_signoff=sig, valuation_signoff=sig)
    for n in range(limit):
        case.put(code=f'active-{n}', request_id=f'active-{n}', state=state, **extra)
    with pytest.raises(ValueError, match='pool capacity reached'):
        case.put(code='over-capacity', request_id='over-capacity', state=state, **extra)
    # Maintaining the same member does not consume another active slot.
    case.put(code='active-0', request_id='maintain', expected_version=1, state=state, **extra)
    assert snapshot(case.scope, case.e['scope_epoch'], case.now)['counts'][state] == limit


def test_large_pending_history_keeps_independent_round_budgets(case):
    seed_pending(case, 105)
    scope = load(case.scope)
    records = [{'code': f'new-{n}', 'channel_risk': {'TREND': {'status': 'unknown'}}}
               for n in range(25)]
    outside, batch, _ = research_queues(records, records, scope)
    reviews = review_queue(scope, records)
    assert len(outside) == 25
    assert len(batch) == len(reviews) == 10
    assert not {r['code'] for r in batch} & {r['code'] for r in reviews}
    assert len(research_queues(records, records, scope, batch_size=3)[1]) == 3
    assert len(review_queue(scope, records, limit=4)) == 4
    assert snapshot(case.scope, case.e['scope_epoch'], case.now)['counts']['pending'] == 105
