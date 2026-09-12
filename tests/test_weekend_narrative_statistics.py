"""Synthetic read-only regression fixtures; never write real event/perf ledgers."""
import copy
import datetime as dt
import statistics
import pytest
import narrative_track as nt


def rows(values):
    return [dict(event_ts=f'synthetic-{i}', code=f'TEST{i}', name='SYNTHETIC',
                 baseline_date=dt.date.today().strftime('%Y%m%d'),
                 event_trade_date=dt.date.today().strftime('%Y%m%d'),
                 days_since_event=14, excess_pct=v, absolute_pct=v,
                 excess_vs_sector=v, hit=(v > 0) if v is not None else False,
                 hit_strict=(v > 0) if v is not None else None,
                 event_score=2, effective_score=2, event_track='test',
                 event_subdomain='test', event_pool='legacy', event_title='SYNTHETIC', side='+')
            for i,v in enumerate(values)]


def setup(monkeypatch, values):
    fixture=rows(values)
    monkeypatch.setattr(nt, '_read_jsonl', lambda p: copy.deepcopy(fixture) if p==nt._PERF_PATH else [])
    return fixture


@pytest.mark.parametrize('values', [[], [None], [2], [1,3], [1,3,9], [1,3,9,11], [-8.15,-2.03], [None,1,3]])
def test_standard_median_all_report_paths(monkeypatch, values):
    fixture=setup(monkeypatch,values);report=nt.report()
    valid=[v for v in values if v is not None]
    expected=round(statistics.median(valid),2) if valid else None
    assert report['main_signal_summary']['median_excess_pct']==expected
    assert report['main_signal_summary']['median_excess_vs_sector']==expected
    for bucket in ['by_milestone','by_score','by_effective_score','by_pool','by_track','by_subdomain','by_event_type','by_late_stage']:
        for r in report[bucket].values():
            assert r['median_excess_pct']==expected
            assert r['median_excess_vs_sector']==expected
    for bucket in ['by_score_fixed_t14','by_effective_score_fixed_t14']:
        for r in report[bucket].values():assert r['median_excess_pct']==expected
    assert fixture==rows(values)


def test_no_false_hit_labels_or_efficacy_claims(monkeypatch):
    setup(monkeypatch,[-8.15,-2.03]);text=nt.doc_markdown()
    assert 'TOP 5 命中' not in text
    assert 'TOP 5 失败' not in text
    assert '真 alpha' not in text
    assert 'alpha 在此 unlock' not in text
    assert '雷达无 alpha' not in text
    assert '18:45 工作日' not in text
    assert '相对收益排序' in text
    assert '-5.09%' in text


def test_side_minus_ranking_is_not_hit_classification(monkeypatch):
    fixture=setup(monkeypatch,[-2,1]);fixture[0].update(side='-',hit=True,hit_strict=True)
    monkeypatch.setattr(nt,'_read_jsonl',lambda p:copy.deepcopy(fixture) if p==nt._PERF_PATH else [])
    r=nt.report();assert r['by_milestone']['T+14']['hits']==2
    assert r['top_losers'][0]['hit'] is True
    assert 'TOP 5 失败' not in nt.doc_markdown()
