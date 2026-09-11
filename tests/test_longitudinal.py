import json
from pathlib import Path
from unittest.mock import patch
import pytest
from mt1.longitudinal import archive, weekly_index, stable_id


@pytest.fixture(autouse=True)
def fixed_archive_clock(monkeypatch):
    from datetime import datetime, timezone
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026,9,11,12,0,tzinfo=timezone.utc)
    monkeypatch.setattr('mt1.longitudinal.datetime',Clock)


def save(root, **kw):
    return archive(root=root,job='test',run_id='day-run',trade_date='2026-09-11',scope_epoch='epoch1',result={'status':'ok'},**kw)


def forecast(**kw):
    return {'kind':'forecast','origin_id':'CN-morning-0911','status':'pending',
            'published_at':'2026-09-11T08:00:00+08:00','target_at':'2026-09-11T09:30:00+08:00',
            'forecast_material':'forecast.md','original_judgment':'测试预测原稿',
            'verification_target':'当日走势','due_date':'2026-09-11',**kw}


def test_archive_content_before_cache_overwrite(tmp_path):
    cache=tmp_path/'handoff.md';cache.write_text('original actual bytes')
    receipt=save(tmp_path/'archive',materials=[{'name':'handoff.md','path':str(cache),'url':'https://example.test'}])
    cache.write_text('replacement')
    parent=Path(receipt['manifest']).parent
    assert (parent/'materials/handoff.md').read_text()=='original actual bytes'
    assert json.loads((parent/'manifest.json').read_text())['materials'][0]['status']=='archived'


def test_same_day_revisions_immutable(tmp_path):
    a=save(tmp_path,materials=[{'name':'a','content':'one'}]);old=Path(a['manifest']).read_bytes()
    b=save(tmp_path,materials=[{'name':'a','content':'two'}])
    assert a['revision']==1 and b['revision']==2
    assert Path(a['manifest']).read_bytes()==old
    assert Path(a['manifest']).parent.joinpath('materials/a').read_text()=='one'


def test_url_not_content(tmp_path):
    r=save(tmp_path,materials=[{'name':'paywall','url':'https://example.test','reason':'access restricted'}])
    d=json.loads(Path(r['manifest']).read_text())
    assert d['missing_materials']==['paywall'] and d['materials'][0]['status']=='unavailable'
    assert not (Path(r['manifest']).parent/'materials/paywall').exists()


def test_forecast_requires_bytes_and_earlier_timestamp(tmp_path):
    with pytest.raises(ValueError,match='original bytes'):
        save(tmp_path,events=[forecast()],materials=[{'name':'forecast.md','url':'https://example.test'}])
    with pytest.raises(ValueError,match='predate'):
        save(tmp_path,events=[forecast(published_at='2026-09-11T10:00:00+08:00')],materials=[{'name':'forecast.md','content':'test'}])


def test_cross_week_pending_overdue_not_completed(tmp_path):
    save(tmp_path,events=[forecast()],materials=[{'name':'forecast.md','content':'test original'}])
    index=weekly_index(tmp_path,asof='2026-10-03',current_epoch='epoch2')
    assert len(index['pending'])==1 and index['pending'][0]['overdue']
    assert index['pending'][0]['historical_epoch'] and index['pending'][0]['status']=='pending'
    assert len(index['forecast_originals'])==1
    assert index['pending'][0]['entity_id']==stable_id('forecast','CN-morning-0911','epoch1')


def test_original_cannot_rewrite(tmp_path):
    save(tmp_path,events=[forecast()],materials=[{'name':'forecast.md','content':'first'}])
    with pytest.raises(ValueError,match='immutable'):
        save(tmp_path,events=[forecast(original_judgment='rewrite')],materials=[{'name':'forecast.md','content':'second'}])


def test_outcome_requires_material_and_keeps_history(tmp_path):
    save(tmp_path,events=[forecast()],materials=[{'name':'forecast.md','content':'first'}])
    with pytest.raises(ValueError,match='observation material'):
        save(tmp_path,events=[forecast(status='confirmed')],materials=[{'name':'forecast.md','content':'first'}])
    save(tmp_path,events=[forecast(status='confirmed',observation_materials=['actual.json'])],materials=[{'name':'forecast.md','content':'first'},{'name':'actual.json','content':'actual'}])
    idx=weekly_index(tmp_path,asof='2026-10-03')
    assert idx['pending']==[] and len(idx['closed_history'])==1 and len(idx['forecast_originals'])==2


def test_archive_failure_no_latest_pointer(tmp_path):
    with patch('mt1.longitudinal.atomic_json',side_effect=OSError('disk failure')):
        with pytest.raises(OSError):save(tmp_path,materials=[{'name':'a','content':'real'}])
    assert not (tmp_path/'latest.json').exists()
    assert weekly_index(tmp_path)['run_count']==0


def test_no_path_escape(tmp_path):
    with pytest.raises(ValueError,match='unsafe'):
        save(tmp_path,materials=[{'name':'../escape','content':'x'}])


def test_original_forecast_bytes_cannot_change_under_same_name(tmp_path):
    save(tmp_path,events=[forecast()],materials=[{'name':'forecast.md','content':'original'}])
    with pytest.raises(ValueError,match='forecast_sha256'):
        save(tmp_path,events=[forecast(status='blocked')],materials=[{'name':'forecast.md','content':'edited'}])


def test_weekly_detects_missing_or_corrupt_archived_material(tmp_path):
    r=save(tmp_path,materials=[{'name':'a','content':'first'}])
    blob=Path(r['manifest']).parent/'materials/a'
    blob.write_text('tampered test fixture')
    assert len(weekly_index(tmp_path,asof='2026-10-03')['integrity_errors'])==1
    blob.unlink()
    assert len(weekly_index(tmp_path,asof='2026-10-03')['integrity_errors'])==1


def test_parallel_archives_allocate_unique_revisions(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=4) as workers:
        receipts=list(workers.map(lambda n:save(tmp_path,materials=[{'name':'n','content':str(n)}]),range(4)))
    assert sorted(r['revision'] for r in receipts)==[1,2,3,4]
    assert weekly_index(tmp_path,asof='2026-10-03')['run_count']==4


def test_daily_archive_failure_does_not_overwrite_handoff(tmp_path,monkeypatch):
    import importlib.util,sys
    spec=importlib.util.spec_from_file_location('mt1_cli',Path(__file__).resolve().parents[1]/'mt1.py')
    cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
    scope=tmp_path/'scope.json'
    scope.write_text(json.dumps({'scope_epoch':'test','reset_at':'2026-09-11T10:00:00+00:00','confirmed_holdings':[],'active_candidates':[]}))
    monkeypatch.setenv('MT1_TRACKING_SCOPE',str(scope))
    out=tmp_path/'handoff.json';out.write_text('original handoff')
    monkeypatch.setattr(sys,'argv',['mt1','--scope',str(scope),'daily-track','--archive-root',str(tmp_path/'archive'),'--out',str(out)])
    with patch('mt1.daily_tracking.track',return_value={'inputs':{},'rows':[],'status':'ok'}), patch('mt1.longitudinal.archive',side_effect=OSError('disk full')):
        with pytest.raises(OSError):cli.main()
    assert out.read_text()=='original handoff'
    monkeypatch.delenv('MT1_TRACKING_SCOPE',raising=False)
