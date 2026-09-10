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


def collect_universe(asof, directory, limit=0):
    """All listed CN stocks, never HOT filtered. Per-stock checkpoints allow resume.

    Current snapshots only: not a historical survivorship-free universe. Financial
    publication date is requested explicitly; absent data cannot become candidates.
    """
    directory = Path(directory)/asof
    directory.mkdir(parents=True, exist_ok=True)
    def stage(name, callback):
        path=directory/(name+'.json')
        if path.exists(): return json.loads(path.read_text())
        data=callback(); atomic_json(path,data); return data
    universe=stage('universe',lambda:api('stock_basic',list_status='L',fields='ts_code,name,industry,list_date'))
    basics=stage('daily_basic',lambda:api('daily_basic',trade_date=asof.replace('-',''),fields='ts_code,trade_date,pe_ttm,pb,total_mv,turnover_rate'))
    basic_by={r['ts_code']:r for r in basics}
    observations=[]; errors=[]
    for stock in universe[:limit or None]:
        code=stock['ts_code']
        try:
            financial=stage('fina-'+code,lambda:api('fina_indicator',ts_code=code,
                fields='ts_code,ann_date,end_date,roe,netprofit_yoy,ocfps,eps,debt_to_assets'))
            observations.append({'stock':stock,'daily':basic_by.get(code,{}),'financials':financial})
        except Exception as e:
            errors.append({'code':code,'reason':str(e)})
    return {'asof':asof,'source':'Tushare current stock_basic + daily_basic + fina_indicator',
            'universe_size':len(universe),'observations':observations,'errors':errors,
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
