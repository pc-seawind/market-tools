"""Work item regression fixtures; never write production plans or send reports."""
from datetime import date, datetime, timedelta
import importlib
import pytest
from test_mt1 import final_plan, method_lookup, calendar
from mt1.plans import reduce_plan


@pytest.mark.parametrize('evidence', ['unverified', ['unverified'],
    [{'url':'https://example.com','date':'2099-01-01','claim':'future'}]])
def test_review_extension_requires_structured_evidence(evidence):
    p=final_plan()
    with pytest.raises(ValueError, match='review.*evidence'):
        reduce_plan(p, {'review_due':str(date.today()+timedelta(days=29)),
                        'extension_evidence':evidence}, method_lookup)


def test_review_extension_accepts_valid_evidence_without_resetting_horizon():
    p=final_plan()
    result=reduce_plan(p, {'review_due':str(date.today()+timedelta(days=29)),
                           'extension_evidence':p['evidence']}, method_lookup)
    assert result['original_deadline']==p['original_deadline']


@pytest.mark.parametrize('phase,allowed', [('morning',True),('evening',False),
                                          ('saturday',True),('sunday',True)])
def test_finalize_uses_phase_gate_before_session_close(tmp_path,monkeypatch,phase,allowed):
    module=importlib.import_module('mt1.finalize')
    fixed=datetime.fromisoformat('2026-09-11T10:00:00+08:00')
    class Clock:
        @staticmethod
        def now(tz=None):return fixed
    monkeypatch.setattr(module,'datetime',Clock)
    monkeypatch.setattr(module,'cn_calendar',lambda now:calendar(now))
    # Isolate calendar routing from plan qualification, tested separately. Real
    # gate, SQLite, report writer run in an isolated temporary directory.
    monkeypatch.setattr(module,'reduce_plan',lambda old,patch,lookup:patch)
    p=final_plan()
    result=module.finalize({'phase':phase,'reviewer':'offline-test',
        'plan_events':[{'id':'p1','expected_version':0,'request_id':'phase-test',
                        'payload':p,'reason':'synthetic local-only test'}]},
        tmp_path/'state',tmp_path/'investment')
    assert bool(result['changes']) is allowed
    if not allowed:assert 'market gate closed' in result['errors'][0]['error']


@pytest.mark.parametrize('phase',['saturday','sunday'])
def test_weekend_research_does_not_allow_closed_market_buy(tmp_path,monkeypatch,phase):
    module=importlib.import_module('mt1.finalize')
    fixed=datetime.fromisoformat('2026-09-12T10:00:00+08:00')
    class Clock:
        @staticmethod
        def now(tz=None):return fixed
    monkeypatch.setattr(module,'datetime',Clock)
    monkeypatch.setattr(module,'cn_calendar',lambda now:calendar(now))
    result=module.finalize({'phase':phase,'reviewer':'offline-test',
        'plan_events':[{'id':'p1','expected_version':0,'request_id':'closed-buy',
                        'payload':final_plan(),'reason':'synthetic local-only test'}]},
        tmp_path/'state',tmp_path/'investment')
    assert not result['changes']
    assert 'market gate closed' in result['errors'][0]['error']


@pytest.mark.parametrize('action',['SELL','EXIT'])
@pytest.mark.parametrize('qualification',['pending_review','final'])
@pytest.mark.parametrize('evidence', [
    [{'url':'https://example.com','date':str(date.today()),'claim':'  '}],
    [{'url':'https://example.com','date':'not-a-date','claim':'synthetic'}],
    [{'date':str(date.today()),'claim':'synthetic'}]])
def test_all_exit_actions_validate_claim_date_and_source(action,qualification,evidence):
    p=final_plan()
    p.update(state=action,held_direction=action,unheld_direction='WATCH',
             qualification=qualification,exit_basis='thesis_invalidated',evidence=evidence)
    with pytest.raises(ValueError,match='invalid exit'):reduce_plan(None,p)
