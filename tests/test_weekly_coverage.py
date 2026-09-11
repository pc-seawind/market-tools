"""Isolated synthetic calendar/execution probes; never production evidence."""
import json
from datetime import datetime, timezone
from pathlib import Path
import pytest
from mt1.longitudinal import archive, weekly_index


def save(tmp_path, monkeypatch, at='2026-09-11T12:00:00+00:00', status='ok', materials=(), execution=None):
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):return datetime.fromisoformat(at)
    monkeypatch.setattr('mt1.longitudinal.datetime',Clock)
    return archive(root=tmp_path,job='daily',run_id='run1',trade_date='2026-09-11',scope_epoch='ep',
                   result={'status':status},materials=materials,execution=execution)


def item(**kw):
    return {'execution_id':'exec1','job':'daily','run_id':'run1','scope_epoch':'ep','market':'CN','trade_date':'2026-09-11',
            'scheduled_at':'2026-09-11T10:00:00+00:00','deadline_at':'2026-09-11T13:00:00+00:00',
            'calendar':{'market':'CN','date':'2026-09-11','is_open':True,'source':'synthetic exchange calendar','verified_at':'2026-09-11T09:00:00+00:00'},**kw}


def execution(**kw):
    return {'execution_id':'exec1','started_at':'2026-09-11T10:01:00+00:00','completed_at':'2026-09-11T11:00:00+00:00','status':'ok','source':'synthetic runner receipt',**kw}


@pytest.mark.parametrize('recorded,included',[
    ('2026-09-11T15:59:59+00:00',True),('2026-09-11T16:00:00+00:00',False),
    ('2026-09-11T17:00:00+00:00',False),('2026-09-12T01:00:00+08:00',False),
    ('2026-09-12T00:00:00+00:00',False)])
def test_beijing_day_boundary(tmp_path,monkeypatch,recorded,included):
    save(tmp_path,monkeypatch,recorded)
    assert weekly_index(tmp_path,asof='2026-09-11')['run_count']==int(included)


def test_explicit_instant_cutoff(tmp_path,monkeypatch):
    save(tmp_path,monkeypatch,'2026-09-11T17:00:00+00:00')
    assert weekly_index(tmp_path,asof='2026-09-12T00:59:59+08:00')['run_count']==0
    assert weekly_index(tmp_path,asof='2026-09-12T01:00:00+08:00')['run_count']==1
    with pytest.raises(ValueError,match='timezone'):weekly_index(tmp_path,asof='2026-09-12T01:00:00')


def test_blocked_and_missing_materials_are_anomalies(tmp_path,monkeypatch):
    r=save(tmp_path,monkeypatch,status='blocked',materials=[{'name':'source','url':'https://example.test'}])
    index=weekly_index(tmp_path,asof='2026-09-11')
    assert index['status']=='partial' and index['failed_runs']==[r['manifest']]
    assert index['anomalies']['unfinished_runs'][0]['status']=='blocked'
    assert index['anomalies']['material_gaps']


def test_integrity_error_is_top_level_anomaly(tmp_path,monkeypatch):
    r=save(tmp_path,monkeypatch)
    (Path(r['manifest']).parent/'result.json').write_text('test-corruption')
    index=weekly_index(tmp_path,asof='2026-09-11')
    assert index['status']=='partial' and index['anomalies']['integrity_errors']


def coverage(tmp_path,expected, executions=(),asof='2026-09-11'):
    return weekly_index(tmp_path,asof=asof,expected={'expected':expected,'executions':list(executions)})['coverage']


def test_missing_entire_job(tmp_path):
    c=coverage(tmp_path,[item()])
    assert c['counts']=={'missing_execution_and_archive':1} and c['status']=='partial'


def test_execution_without_archive(tmp_path):
    assert coverage(tmp_path,[item()],[execution()])['counts']=={'missing_archive':1}


def test_archive_not_proof_of_execution(tmp_path,monkeypatch):
    save(tmp_path,monkeypatch)
    assert coverage(tmp_path,[item()])['counts']=={'execution_unverified':1}


def test_matching_actual_execution_and_archive(tmp_path,monkeypatch):
    save(tmp_path,monkeypatch,execution=execution())
    assert coverage(tmp_path,[item()])['counts']=={'covered':1}


def test_external_runner_evidence_matches_archive(tmp_path,monkeypatch):
    save(tmp_path,monkeypatch)
    assert coverage(tmp_path,[item()],[execution()])['counts']=={'covered':1}


def test_late_execution_not_silent_pass(tmp_path,monkeypatch):
    save(tmp_path,monkeypatch,at='2026-09-11T14:30:00+00:00',execution=execution(completed_at='2026-09-11T14:00:00+00:00'))
    assert coverage(tmp_path,[item()])['counts']=={'late':1}


def test_future_execution_never_covered(tmp_path,monkeypatch):
    save(tmp_path,monkeypatch,execution=execution(completed_at='2026-09-12T01:00:00+00:00'))
    assert coverage(tmp_path,[item()])['counts']=={'execution_unverified':1}


def test_not_yet_due_is_not_missing(tmp_path):
    assert coverage(tmp_path,[item()],asof='2026-09-11T12:00:00+00:00')['counts']=={'not_due':1}


def test_calendars_independent_no_weekday_fallback(tmp_path):
    # Same Friday: explicit CN closed, HK open/missing, US unknown calendar.
    cn=item(calendar={'market':'CN','date':'2026-09-11','is_open':False,'source':'synthetic holiday','verified_at':'2026-09-10T12:00:00+00:00'})
    hk=item(execution_id='hk',market='HK',calendar={'market':'HK','date':'2026-09-11','is_open':True,'source':'synthetic HK open','verified_at':'2026-09-10T12:00:00+00:00'})
    us=item(execution_id='us',market='US',calendar={})
    assert coverage(tmp_path,[cn,hk,us])['counts']=={'not_expected_market_closed':1,'missing_execution_and_archive':1,'calendar_or_schedule_unverified':1}


def test_old_epoch_does_not_cover_new_execution(tmp_path,monkeypatch):
    save(tmp_path,monkeypatch,execution=execution())
    assert coverage(tmp_path,[item(scope_epoch='new')])['counts']=={'missing_execution_and_archive':1}


def test_bad_or_future_calendar_fail_closed(tmp_path):
    c=item(calendar={'market':'CN','date':'2026-09-11','is_open':True,'source':'future','verified_at':'2026-09-12T01:00:00+00:00'})
    assert coverage(tmp_path,[c])['counts']=={'calendar_or_schedule_unverified':1}


def test_incomplete_archive_not_covered(tmp_path,monkeypatch):
    save(tmp_path,monkeypatch,status='blocked',execution=execution())
    assert coverage(tmp_path,[item()])['counts']=={'archive_incomplete':1}


def test_material_gap_archive_cannot_be_covered(tmp_path,monkeypatch):
    save(tmp_path,monkeypatch,execution=execution(),materials=[{'name':'lost','path':str(tmp_path/'not-present')}])
    c=coverage(tmp_path,[item()])
    assert c['counts']=={'archive_incomplete':1}
    assert c['expected_count']==1 and c['observed_archive_count']==1 and c['observed_execution_count']==1


def test_coverage_counts_only_due_calendar_verified_jobs(tmp_path):
    pending=item(execution_id='later',deadline_at='2026-09-12T13:00:00+00:00')
    unknown=item(execution_id='unknown',calendar={})
    c=coverage(tmp_path,[item(),pending,unknown])
    assert c['scheduled_count']==3 and c['expected_count']==1
    assert c['observed_archive_count']==0 and c['missing_archive_count']==1 and c['unverified_calendar_count']==1
