"""Reopen must not bypass a bound trigger; all scopes/evidence are synthetic."""
from datetime import timedelta

import pytest

from mt1 import research_tasks as tasks, research_pool as pool
from mt1.scope import load
from test_research_tasks import case, task, evidence, get


def park(c, action):
    task(c, due=(c.now + timedelta(days=7)).isoformat())
    check = c.now + timedelta(days=3)
    task(c, action, disposition_basis='等指定新版财报，不是任意新材料', next_action='核对指定财报',
         trigger_key='q3-filing', trigger_condition='指定Q3财报发布', check_at=check.isoformat())
    return get(c), check


@pytest.mark.parametrize('action,status', [('wait', 'waiting'), ('block', 'blocked')])
@pytest.mark.parametrize('trigger', [{}, {'trigger_key': ''}, {'trigger_key': None},
                                     {'trigger_key': 'unrelated-event'}],
                         ids=['missing', 'empty', 'null', 'mismatch'])
def test_bound_trigger_cannot_be_omitted_or_replaced(case, action, status, trigger):
    before, check = park(case, action)
    history = tasks.timeline(case.scope, case.e['scope_epoch'])
    src = evidence(case, 'SYNTHETIC arbitrary new bytes do not prove the bound trigger')
    assert case.now < check and before['status'] == status
    with pytest.raises(ValueError, match='matching original trigger'):
        task(case, 'reopen', sources=[src], **trigger)
    assert get(case) == before
    assert tasks.timeline(case.scope, case.e['scope_epoch']) == history
    assert pool.review_queue(load(case.scope)) == []
    assert case.scope.read_bytes() == case.before


@pytest.mark.parametrize('action', ['wait', 'block'])
def test_matching_trigger_with_new_evidence_reopens_before_check(case, action):
    before, check = park(case, action)
    src = evidence(case, 'SYNTHETIC evidence for q3-filing')
    task(case, 'reopen', sources=[src], trigger_key='q3-filing')
    after = get(case)
    assert case.now < check and after['status'] == 'active'
    assert after['generation'] == before['generation'] + 1
    assert after['version'] == before['version'] + 1
    assert after['original_due'] == after['due'] == before['due']
    assert after['trigger_key'] == 'q3-filing'


@pytest.mark.parametrize('action,status', [('wait', 'waiting'), ('block', 'blocked')])
def test_check_at_still_requires_explicit_disposition_review(case, action, status):
    before, check = park(case, action)
    case.now = check
    snap = tasks.snapshot(case.scope, case.e['scope_epoch'], case.now)
    after = get(case)
    assert after['status'] == 'review_required' and after['parked_from'] == status
    assert after['version'] == before['version'] and after['generation'] == 1
    assert after['due'] == before['due'] and after['trigger_key'] == 'q3-filing'
    assert snap['accounting']['reopened'] == 1 and snap['accounting']['reconciled']
    assert len(tasks.timeline(case.scope, case.e['scope_epoch'])) == 2
    scope = load(case.scope); scope['research_pool']['tasks'] = snap
    assert pool.review_queue(scope)[0]['code'] == case.e['code']
    with pytest.raises(ValueError, match='explicit reopen requires parked/completed'):
        task(case, 'reopen', sources=[evidence(case, 'SYNTHETIC at check time')], trigger_key='q3-filing')
    task(case, 'review', decision='continue', open_gaps=['cash_bridge'], next_action='向披露方核对现金桥',
         due=before['due'], continuation_basis='指定披露已定位，明确向披露方核对现金桥')
    assert get(case)['status'] == 'active' and get(case)['due'] == before['due']


def test_archive_without_original_trigger_reopens_without_fabricated_trigger(case):
    task(case)
    task(case, 'archive', disposition_basis='优先级不足，不绑定外部触发', next_action='新证据显式重开')
    before = get(case)
    assert 'trigger_key' not in before
    src = evidence(case, 'SYNTHETIC genuine new evidence for unbound archive')
    with pytest.raises(ValueError, match='new verifiable evidence'):
        task(case, 'reopen')
    event = task(case, 'reopen', sources=[src])
    assert get(case)['status'] == 'active' and get(case)['generation'] == 2
    assert 'trigger_key' not in event and 'trigger_key' not in get(case)
