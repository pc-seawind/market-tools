"""Run-scoped dated daily_basic panel. Missing/invalid panels fall back per stock.
Only the four current-day fields are projected; historical PE is untouched.
"""
import csv
from datetime import date
from functools import lru_cache
import json
import os
from pathlib import Path
import sys
import time
from recap_runtime import atomic

FIELDS = 'pe_ttm,pb,total_mv,turnover_rate'
BATCH_FIELDS = 'ts_code,trade_date,' + FIELDS
ROOT = Path(__file__).resolve().parent


def valid(value, day, signature):
    return (value.get('status') == 'complete' and value.get('trade_date') == day
            and day <= date.today().strftime('%Y%m%d')
            and value.get('input_hash') == signature
            and value.get('created_day') == date.today().isoformat()
            and 0 <= time.time()-value.get('fetched_at', 0) < 1800)


def prepare(path, day, signature, fetch=None):
    path = Path(path)
    try:
        old = json.loads(path.read_text())
        if valid(old, day, signature): return old
    except (OSError, ValueError, TypeError): pass
    if len(day) != 8 or not day.isdigit() or day > date.today().strftime('%Y%m%d'):
        raise ValueError('invalid_daily_basic_date')
    from recap_rpc import csv_rpc
    fetch = fetch or csv_rpc
    rows = {}
    # Use caller run cache, input signature, existing rate limiter and timeout.
    for page in range(3):
        params = dict(trade_date=day, limit='6000', offset=str(page*6000), fields=BATCH_FIELDS)
        args = ['python3', str(ROOT/'tushare.py'), 'daily_basic', f'trade_date={day}',
                'limit=6000', f'offset={page*6000}', f'--fields={BATCH_FIELDS}', '--csv']
        items = fetch(args, 'daily_basic', params)
        if not items and page == 0: raise ValueError('batch_empty_or_failed')
        # Empty later page could be an RPC failure, not pagination exhaustion.
        if not items: raise ValueError('batch_unproven_terminal_page')
        for row in items:
            if (set(BATCH_FIELDS.split(',')) - row.keys() or row['trade_date'] != day
                    or not row['ts_code'] or row['ts_code'] in rows):
                raise ValueError('batch_fields_date_or_duplicate')
            rows[row['ts_code']] = {k: row[k] for k in FIELDS.split(',')}
        if len(items) < 6000: break
    else: raise ValueError('batch_page_budget_exhausted')
    value = dict(status='complete', trade_date=day, input_hash=signature,
                 created_day=date.today().isoformat(), fetched_at=time.time(), rows=rows,
                 pages=page+1, fields=FIELDS)
    atomic(path, value)
    return value


@lru_cache(maxsize=4)
def _load(path, stamp):
    return json.loads(Path(path).read_text())


def project(api, params):
    path = os.getenv('RECAP_DAILY_BASIC')
    if (not path or api != 'daily_basic' or params.get('fields') != FIELDS
            or os.getenv('TUSHARE_NO_CACHE') == '1'):
        return None
    if set(params) != {'fields', 'ts_code', 'trade_date'}: return None
    try:
        # Never let a newer panel silently replace a valid exact single-query
        # snapshot (including historical permanent caches / provider revisions).
        import tushare
        query = {k:str(v) for k,v in params.items() if k != 'fields'}
        if tushare._cache_read(api, query, FIELDS) is not None: return None
        value = _load(path, Path(path).stat().st_mtime_ns)
        if not valid(value, params['trade_date'], os.getenv('RECAP_INPUT_HASH', '')): return None
        row = value['rows'].get(params['ts_code'])
        if row is None or set(row) != set(FIELDS.split(',')): return None
        return [dict(row)]
    except (OSError, ValueError, TypeError, KeyError): return None


if __name__ == '__main__':
    try:
        value = prepare(sys.argv[1], sys.argv[2], os.environ['RECAP_INPUT_HASH'])
        print(json.dumps({'status': value['status'], 'rows': len(value['rows']), 'pages': value['pages']}))
    except Exception as exc:
        print(json.dumps({'status': 'per_stock_fallback', 'reason': str(exc)}))
        sys.exit(1)
