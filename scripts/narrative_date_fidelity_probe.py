#!/usr/bin/env python3
"""Read-only live supplier probe; verify writes only into a temporary ledger.

Usage: python3 scripts/narrative_date_fidelity_probe.py --out <NEW directory>
Uses production fetch/verify functions and genuine providers, not mock prices.
Tushare requests add --no-cache and disable retry, keeping production caches intact.
"""
import argparse
import copy
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--out', type=Path, required=True)
    args = ap.parse_args(); out = args.out.resolve(); out.mkdir(parents=True, exist_ok=False)
    os.environ['TUSHARE_NO_RETRY'] = '1'
    os.environ['TUSHARE_NO_CACHE'] = '1'
    import narrative_track as nt
    import narrative_sector_bench as sb
    import quote_sources as qs
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    protected = json.loads((HERE/'reports/narrative-date-fidelity-20261001-r2/protected-before.json').read_text())
    # Resolve relative manifest paths independently of the caller's cwd.
    protected = {str((HERE/Path(p)).resolve()): h for p, h in protected.items()}
    assert all(sha(p) == h for p, h in protected.items())
    calls = []
    run = subprocess.run
    bars = qs.daily_bars

    def save(name, value):
        (out/name).write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n')

    def traced_run(cmd, *a, **kw):
        if not isinstance(cmd, list) or str(nt._TUSHARE) not in cmd:
            return run(cmd, *a, **kw)
        cmd = [*cmd, '--no-cache']
        start = time.monotonic(); stamp = dt.datetime.now(dt.timezone.utc).isoformat()
        try:
            result = run(cmd, *a, **kw)
        except subprocess.TimeoutExpired:
            calls.append(dict(kind='tushare-cli-live-no-cache', argv=cmd, started_at=stamp,
                              error='TimeoutExpired', elapsed_seconds=round(time.monotonic()-start, 4)))
            save('provider-calls.json', calls)
            raise
        item = dict(kind='tushare-cli-live-no-cache', argv=cmd, started_at=stamp,
                    elapsed_seconds=round(time.monotonic()-start, 4), returncode=result.returncode,
                    stdout=result.stdout, stderr=result.stderr)
        calls.append(item); save('provider-calls.json', calls)
        return result

    def traced_bars(*a, **kw):
        start = time.monotonic(); stamp = dt.datetime.now(dt.timezone.utc).isoformat()
        result = bars(*a, **kw)
        calls.append(dict(kind='production-quote_sources.daily_bars-live', args=a, kwargs=kw,
                          started_at=stamp, elapsed_seconds=round(time.monotonic()-start, 4), rows=result))
        save('provider-calls.json', calls)
        return result

    subprocess.run = traced_run
    qs.daily_bars = traced_bars
    result = {'started_at': started, 'synthetic_prices': False,
              'instrumentation': 'delegate actual subprocess/quote calls; no response substitution',
              'source_hashes': {p: sha(HERE/p) for p in [
                  'narrative_track.py','narrative_sector_bench.py','narrative_perf_quality.py',
                  'narrative_perf_exclusions.jsonl','quote_sources.py','tushare.py']}}
    try:
        cn = nt._ts_csv('trade_cal', exchange='SSE', start_date='20260924', end_date='20261002')
        hk = nt._ts_csv('hk_tradecal', start_date='20260924', end_date='20261002')
        result['calendars'] = {'CN':cn, 'HK':hk}
        cncal={r['cal_date']:int(r['is_open']) for r in cn}
        hkcal={r['cal_date']:int(r['is_open']) for r in hk}
        assert cncal['20261001']==hkcal['20261001']==0
        assert cncal['20260925']==0 and hkcal['20260925']==1
        with tempfile.TemporaryDirectory(prefix='narrative-live-date-probe-') as temp:
            temp=Path(temp)
            sb._CACHE_DIR=temp/'sector-cache';sb._CACHE_DIR.mkdir()
            sb._DAILY_SNAPSHOT_CACHE={}
            nt._PERF_PATH=temp/'perf.jsonl';nt._EVENTS_PATH=temp/'events.jsonl'
            append = nt._append_jsonl
            def isolated_append(path, record):
                assert Path(path).resolve().is_relative_to(temp.resolve())
                append(path,record)
            nt._append_jsonl=isolated_append
            incident=nt._read_jsonl(HERE/'.cron_state/narrative-track-daily/20261001-1945/perf-appended.jsonl')
            selected={}
            for row in incident:selected.setdefault(row['event_ts'],set()).add(row['code'])
            events=[]
            for ev in nt._read_jsonl(HERE/'narrative_events.jsonl'):
                if ev['ts'] in selected:
                    ev=copy.deepcopy(ev)
                    for key in ('tickers','alpha_tickers'):
                        if key in ev:ev[key]=[t for t in ev[key] if t['code'] in selected[ev['ts']]]
                    events.append(ev)
            assert len(events)==len(selected)
            nt._EVENTS_PATH.write_text(''.join(json.dumps(e)+'\n' for e in events))
            nt._PERF_PATH.write_bytes((HERE/'narrative_perf.jsonl').read_bytes())
            before=sha(nt._PERF_PATH)
            stats=nt.verify_all('20261001',mode='full')
            result['incident_isolated_verify']={'stats':stats,'ledger_before':before,
                                               'ledger_after':sha(nt._PERF_PATH)}
            assert stats['verified']==0 and stats['fetch_failed']==4
            assert sha(nt._PERF_PATH)==before
            result['target_benchmarks']={m:nt._fetch_benchmark_close(m,'20261001') for m in ('A','HK')}
            assert all(v[1] is None for v in result['target_benchmarks'].values())
            result['prior_cn_closes']={r['code']:nt._fetch_stock_close(r['code'],'20260930') for r in incident}
            assert all(result['prior_cn_closes'][r['code']]==r['current_price'] for r in incident)
            result['independent_market_cases']=[]
            # Synthetic EVENT only; prices/calendars/base resolution are live production fetches.
            for day,base,codes,want in [('20261001','20260930',['00700.HK'],0),
                                      ('20260925','20260924',['300274.SZ','00700.HK'],1)]:
                event=dict(ts='ISOLATED_LIVE_PROBE_'+day,trade_date=base,pub_date=base,
                           session='intraday',score=3,tickers=[{'code':c} for c in codes])
                nt._EVENTS_PATH.write_text(json.dumps(event)+'\n')
                nt._PERF_PATH.write_text('')
                stats=nt.verify_all(day,mode='full')
                rows=nt._read_jsonl(nt._PERF_PATH)
                case={'date':day,'event_is_synthetic':True,'prices_are_live':True,'stats':stats,'appended':rows}
                result['independent_market_cases'].append(case)
                assert stats['verified']==want and stats['fetch_failed']==len(codes)-want
                assert len(rows)==want
                if want:
                    assert rows[0]['code']=='00700.HK' and rows[0]['verify_date']==day
                    assert rows[0]['benchmark_current'] and rows[0]['excess_pct'] is not None
            result['passed']=True
    except Exception as exc:
        result['passed']=False;result['error']=f'{type(exc).__name__}: {exc}'
        raise
    finally:
        subprocess.run=run;qs.daily_bars=bars
        result['completed_at']=dt.datetime.now(dt.timezone.utc).isoformat()
        result['protected_unchanged']={p:sha(p)==h for p,h in protected.items()}
        result['all_protected_unchanged']=all(result['protected_unchanged'].values())
        save('result.json',result)
        assert result['all_protected_unchanged']
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
