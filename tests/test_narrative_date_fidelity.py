"""Synthetic market sessions; HK availability is never inferred from CN's date."""
import copy
import json
from pathlib import Path
import pytest
import narrative_track as nt
import narrative_sector_bench as sb
from narrative_perf_quality import filter_perfs, record_hash


@pytest.mark.parametrize('rows', [[], [{'ts_code':'000001.SZ','trade_date':'20260930','close':'10'}],
                                  [{'ts_code':'000001.SZ','close':'10'}]])
def test_snapshot_never_falls_back(monkeypatch, rows):
    calls=[]
    monkeypatch.setattr(sb, '_DAILY_SNAPSHOT_CACHE', {})
    monkeypatch.setattr(sb, '_ts_csv', lambda api, **kw: calls.append(kw) or rows)
    assert sb._fetch_daily_snapshot('20261001') == {}
    assert calls == [{'trade_date':'20261001'}]


def test_snapshot_exact_and_cache_version(monkeypatch, tmp_path):
    monkeypatch.setattr(sb, '_DAILY_SNAPSHOT_CACHE', {})
    monkeypatch.setattr(sb, '_ts_csv', lambda *a, **k: [
        {'ts_code':'000001.SZ','trade_date':'2026-09-30','close':'10'}])
    assert sb._fetch_daily_snapshot('20260930') == {'000001.SZ':10}
    monkeypatch.setattr(sb, '_CACHE_DIR', tmp_path)
    (tmp_path/'test__20260930__20261001.json').write_text('{"sector_pct":99}')
    monkeypatch.setattr(sb, 'industry_for', lambda c:'test')
    monkeypatch.setattr(sb, '_members_of', lambda i:['000001.SZ'])
    monkeypatch.setattr(sb, '_fetch_daily_snapshot', lambda d:{} if d=='20261001' else {'000001.SZ':10})
    assert sb.compute_sector_perf('000001.SZ','20260930','20261001') is None


@pytest.mark.parametrize('code', ['000001.SZ','00700.HK'])
@pytest.mark.parametrize('field', ['date','trade_date'])
@pytest.mark.parametrize('date,expected', [('2026-09-30',None),('2026-10-01',12),('',None)])
def test_daily_bars_date_check(monkeypatch, code, field, date, expected):
    import quote_sources
    monkeypatch.setattr(sb, '_fetch_daily_snapshot', lambda d:{})
    monkeypatch.setattr(nt, '_ts_csv', lambda *a, **kw:[])
    monkeypatch.setattr(quote_sources, 'daily_bars', lambda *a, **kw:[{field:date,'close':12}])
    assert nt._fetch_stock_close(code,'20261001') == expected


@pytest.mark.parametrize('market', ['A','HK'])
@pytest.mark.parametrize('date,expected', [('20260930',None),('20261001',4000)])
def test_benchmark_date_check(monkeypatch, market, date, expected):
    monkeypatch.setattr(nt, '_ts_csv', lambda *a, **kw:[{'trade_date':date,'close':4000}])
    assert nt._fetch_benchmark_close(market,'20261001')[1] == expected


def setup_verify(monkeypatch,tmp_path):
    event=dict(ts='synthetic',trade_date='20260930',pub_date='20260930',score=3,
               tickers=[dict(code='000001.SZ'),dict(code='00700.HK')])
    ep=tmp_path/'events.jsonl';ep.write_text(json.dumps(event)+'\n')
    pp=tmp_path/'perf.jsonl'
    monkeypatch.setattr(nt,'_EVENTS_PATH',ep);monkeypatch.setattr(nt,'_PERF_PATH',pp)
    monkeypatch.setattr(nt,'resolve_base',lambda *a:('20260930',10))
    monkeypatch.setattr(nt,'resolve_bench_base',lambda *a:4000)
    monkeypatch.setattr(sb,'compute_sector_perf',lambda *a:dict(sector_pct=1))
    return event,pp


@pytest.mark.parametrize('hk_has_session',[True,False])
def test_oct1_cn_closed_hk_independent(monkeypatch,tmp_path,hk_has_session):
    import quote_sources
    _,pp=setup_verify(monkeypatch,tmp_path)
    monkeypatch.setattr(sb,'_DAILY_SNAPSHOT_CACHE',{})
    monkeypatch.setattr(sb,'_ts_csv',lambda *a,**kw:[])
    def data(api,**kw):
        if hk_has_session and api in ('hk_daily','index_global'):
            return [{'trade_date':'20261001','close':12 if api=='hk_daily' else 4100}]
        return [{'trade_date':'20260930','close':10}]
    monkeypatch.setattr(nt,'_ts_csv',data)
    monkeypatch.setattr(quote_sources,'daily_bars',lambda *a,**kw:[{'date':'2026-09-30','close':10}])
    result=nt.verify_all('20261001',mode='full')
    assert result['verified']==int(hk_has_session)
    assert result['fetch_failed']==2-int(hk_has_session)
    rows=nt._read_jsonl(pp)
    assert [r['code'] for r in rows]==(['00700.HK'] if hk_has_session else [])


@pytest.mark.parametrize('missing',['base','current'])
def test_missing_benchmark_is_failure_not_hit(monkeypatch,tmp_path,missing):
    event,pp=setup_verify(monkeypatch,tmp_path)
    monkeypatch.setattr(nt,'_fetch_stock_close',lambda *a,**kw:12)
    monkeypatch.setattr(nt,'resolve_bench_base',lambda *a:None if missing=='base' else 4000)
    monkeypatch.setattr(nt,'_fetch_benchmark_close',lambda *a,**kw:('HSI',None if missing=='current' else 4100))
    stats=nt.verify_all('20261001',mode='full')
    assert stats['verified']==0 and stats['fetch_failed']==2
    assert not pp.exists()


def test_real_exclusions_all_consumers_and_correction(monkeypatch,tmp_path):
    import narrative_backtest as nb
    import narrative_radar_review as nr
    import backtest_signal_compare as bc
    source=Path(__file__).parent/'fixtures/narrative-invalid-20261001.jsonl'
    original=source.read_bytes();bad=[json.loads(x) for x in original.splitlines()]
    good=copy.deepcopy(bad[-1]);good['verify_ts']='corrected-independent-observation'
    assert record_hash(good)!=record_hash(bad[-1])
    pp=tmp_path/'perf.jsonl';pp.write_bytes(original+(json.dumps(good)+'\n').encode())
    before=pp.read_bytes()
    monkeypatch.setattr(nt,'_PERF_PATH',pp)
    monkeypatch.setattr(nt,'_EVENTS_PATH',tmp_path/'absent-events')
    assert nt._read_perfs()==[good]
    assert nt.report(weeks=52)['quality_audit']['excluded_count']==4
    assert '显式排除 4 条' in nt.doc_markdown(weeks=52)
    assert nt.report(weeks=52)['ticker_pairs']==1
    assert nt._prewarm_verify_state([])[0]=={(good['event_ts'],good['code'],31)}
    assert '无 perf' in nt.event_report(bad[0]['event_ts'])
    monkeypatch.setattr(nb,'PERF',pp)
    assert nb.exact_outcomes(59)=={}
    monkeypatch.setattr(nr,'PERF_PATH',pp)
    assert nr.load_perf_window(0,100)==[good]
    monkeypatch.setattr(bc,'_PERF_JSONL',pp)
    assert bc.load_perf()==[good]
    assert pp.read_bytes()==before and source.read_bytes()==original


def test_exclusion_fail_closed(tmp_path):
    p=tmp_path/'missing'
    with pytest.raises(FileNotFoundError):filter_perfs([],p)
    p.write_text('not json')
    with pytest.raises(ValueError):filter_perfs([],p)


@pytest.mark.parametrize('api', ['index_daily','index_global'])
@pytest.mark.parametrize('date', ['20260930','20261002',''])
def test_benchmark_base_never_borrows_other_session(monkeypatch,api,date):
    monkeypatch.setattr(nt,'_ts_csv',lambda *a,**kw:[{'trade_date':date,'open':4000,'close':4100}])
    assert nt.resolve_bench_base('BENCH',api,'20261001','post') is None


@pytest.mark.parametrize('session,expected', [('post',4000),('pre',4000),('intraday',4100)])
def test_benchmark_base_exact_session(monkeypatch,session,expected):
    monkeypatch.setattr(nt,'_ts_csv',lambda *a,**kw:[{'trade_date':'20261001','open':4000,'close':4100}])
    assert nt.resolve_bench_base('BENCH','index_global','20261001',session)==expected


def test_excluded_only_ledger_all_track_readers(monkeypatch,tmp_path):
    source=Path(__file__).parent/'fixtures/narrative-invalid-20261001.jsonl'
    pp=tmp_path/'perf.jsonl';pp.write_bytes(source.read_bytes())
    monkeypatch.setattr(nt,'_PERF_PATH',pp)
    monkeypatch.setattr(nt,'_EVENTS_PATH',tmp_path/'absent-events')
    rows=[json.loads(x) for x in pp.read_text().splitlines()]
    before=pp.read_bytes()
    assert nt._existing_perf_keys()==set()
    for r in rows:
        assert nt._baseline_for(r['event_ts'],r['code']) is None
        assert nt._benchmark_baseline_for(r['event_ts'],r['code']) is None
        assert '无 perf' in nt.event_report(r['event_ts'])
    assert nt._prewarm_verify_state([])==(set(),{})
    report=nt.report(weeks=52)
    assert report['ticker_pairs']==0 and report['events_covered']==0
    assert report['quality_audit']['excluded_count']==4
    assert not report['by_milestone'] and not report['top_winners'] and not report['top_losers']
    assert '显式排除 4 条' in nt.doc_markdown(weeks=52)
    assert pp.read_bytes()==before
