"""Bounded provider calls and immutable, resumable full-universe snapshots."""
import csv
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from .store import digest

HERE = Path(__file__).resolve().parents[1]


def api(name, **params):
    cmd = [sys.executable, str(HERE/'tushare.py'), name, '--csv']
    for k,v in params.items():
        cmd.append(f'--fields={v}' if k == 'fields' else f'{k}={v}')
    cp = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
    if cp.returncode:
        raise RuntimeError(f'{name}: exit {cp.returncode}: {cp.stderr[-200:]}')
    rows = list(csv.DictReader(cp.stdout.splitlines()))
    if not rows:
        raise ValueError(f'{name}: empty result')
    return rows


def cn_calendar(now):
    from zoneinfo import ZoneInfo
    today = now.astimezone(ZoneInfo('Asia/Shanghai')).date()
    rows = api('trade_cal', exchange='SSE', start_date=(today-timedelta(days=50)).strftime('%Y%m%d'),
               end_date=today.strftime('%Y%m%d'), fields='cal_date,is_open')
    days = []
    for r in rows:
        d = datetime.strptime(r['cal_date'], '%Y%m%d').date().isoformat()
        if r['is_open'] not in ('0','1'):
            raise ValueError('invalid is_open')
        days.append({'date':d, 'is_open':r['is_open']=='1', 'close_at': d+'T15:00:00+08:00'})
    return {'exchange':'SSE', 'source':'Tushare trade_cal exchange=SSE', 'fetched_at':now.isoformat(), 'days':days}


def atomic_json(path, value):
    import os, tempfile
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd,'w') as f:
            json.dump(value,f,ensure_ascii=False,allow_nan=False); f.flush(); os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)


def _collect_universe(asof, directory, limit=0):
    """All listed CN stocks, never HOT filtered. Per-stock checkpoints allow resume.

    Current snapshots only: not a historical survivorship-free universe. Financial
    publication date is requested explicitly; absent data cannot become candidates.
    """
    if limit < 0: raise ValueError('limit must be nonnegative')
    root = Path(directory)
    cursor_path = root/'coverage.json'
    cursor = json.loads(cursor_path.read_text()) if cursor_path.exists() else {'last_seen':{}}
    if not cursor_path.exists():
        # Upgrade existing checkpoints: do not spend the first new batch on the
        # pre-fix first 100 symbols again. This tracks attempts, not fresh coverage.
        for path in root.glob('*/fina-*.json'):
            code=path.stem[5:]
            stamp=datetime.fromtimestamp(path.stat().st_mtime).isoformat()
            cursor['last_seen'][code]=max(stamp,cursor['last_seen'].get(code,''))
        atomic_json(cursor_path,cursor)
    directory = root/asof
    directory.mkdir(parents=True, exist_ok=True)
    def stage(name, callback):
        path=directory/(name+'.json')
        if path.exists(): return json.loads(path.read_text())
        data=callback(); atomic_json(path,data); return data
    universe=stage('universe',lambda:api('stock_basic',list_status='L',fields='ts_code,name,industry,list_date'))
    basics=stage('daily_basic',lambda:api('daily_basic',trade_date=asof.replace('-',''),fields='ts_code,trade_date,pe_ttm,pb,total_mv,turnover_rate'))
    basic_by={r['ts_code']:r for r in basics}
    observations=[]; errors=[]
    # Least-recently attempted first: failures cannot starve later symbols.
    # Same-date completed observations are reconstructed below, not fetched again.
    ordered = sorted(universe, key=lambda s: (cursor['last_seen'].get(s['ts_code'], ''), s['ts_code']))
    selected = ordered[:limit or None]
    for stock in selected:
        code=stock['ts_code']
        try:
            financial=stage('fina-'+code,lambda:api('fina_indicator',ts_code=code,
                fields='ts_code,ann_date,end_date,roe,netprofit_yoy,ocfps,eps,debt_to_assets'))
            observations.append({'stock':stock,'daily':basic_by.get(code,{}),'financials':financial})
        except Exception as e:
            errors.append({'code':code,'reason':str(e)})
        cursor['last_seen'][code] = datetime.now().isoformat()
        atomic_json(cursor_path, cursor)
    # Count only current-date cached observations, never mix dates into coverage.
    observations = []
    for stock in universe:
        path = directory/('fina-'+stock['ts_code']+'.json')
        if path.exists():
            observations.append({'stock':stock,'daily':basic_by.get(stock['ts_code'],{}),
                                 'financials':json.loads(path.read_text())})
    seen = sum(s['ts_code'] in cursor['last_seen'] for s in universe)
    return {'asof':asof,'source':'Tushare current stock_basic + daily_basic + fina_indicator',
            'universe_size':len(universe),'observations':observations,'errors':errors,
            'batch_codes':[s['ts_code'] for s in selected],
            'coverage_progress':{'attempted_unique':seen,'total':len(universe),
                                 'current_date_observed':len(observations),
                                 'cross_date_attempts_are_not_fresh_coverage':True},
            'historical_universe':False,'data_version':digest([universe,basics,observations])}


def foreign_calendar(now, market):
    """Tushare market-specific dates, conservative regular-close completion bound.

    Calendar endpoints do not expose half-day close times. Waiting until 17:00
    local is deliberately conservative (also covers closing auction); not a claim
    to model intraday tradability or exceptional extended sessions.
    Docs: https://tushare.pro/document/2?doc_id=250 and doc_id=253.
    """
    from zoneinfo import ZoneInfo
    from .calendar import MARKETS
    tz,exchange=MARKETS[market]
    if market not in ('HK','US'): raise ValueError('foreign market required')
    local=now.astimezone(ZoneInfo(tz)).date()
    name='hk_tradecal' if market=='HK' else 'us_tradecal'
    rows=api(name,start_date=(local-timedelta(days=50)).strftime('%Y%m%d'),end_date=local.strftime('%Y%m%d'),fields='cal_date,is_open')
    days=[]
    for r in rows:
        d=datetime.strptime(r['cal_date'],'%Y%m%d').date()
        if r['is_open'] not in ('0','1'): raise ValueError('invalid is_open')
        days.append({'date':str(d),'is_open':r['is_open']=='1',
                     'close_at':datetime(d.year,d.month,d.day,17,tzinfo=ZoneInfo(tz)).isoformat()})
    return {'exchange':exchange,'source':'Tushare '+name,'fetched_at':now.isoformat(),'days':days,
            'completion_policy':'conservative_17_local_not_exact_half_day_schedule'}


def collect_universe(asof, directory, limit=0):
    import fcntl
    root = Path(directory); root.mkdir(parents=True, exist_ok=True)
    with (root/'collection.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _collect_universe(asof, directory, limit)
