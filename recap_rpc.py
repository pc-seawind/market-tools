"""Bounded stock RPC fanout, success-only session cache, durable per-API timing."""
import csv
import json
import os
from pathlib import Path
import threading
import time
from datetime import date
from recap_runtime import atomic, digest, run

_slots = threading.BoundedSemaphore(2)
_rate = threading.Lock()
_log_lock = threading.Lock()
_last = 0.0
context = threading.local()


def trace(record):
    path = os.getenv('RECAP_RPC_TRACE')
    if path:
        with _log_lock:
            with open(path, 'a') as f: f.write(json.dumps(record, ensure_ascii=False)+'\n')


def csv_rpc(args, api, params):
    global _last
    started = time.monotonic()
    today = date.today().strftime('%Y%m%d')
    key = digest([today, args, os.getenv('RECAP_INPUT_HASH', 'interactive-v1')])
    base = os.getenv('RECAP_RPC_CACHE')
    path = Path(base)/f'{key}.json' if base else None
    record = {'api': api, 'stock': params.get('ts_code'), 'sector': os.getenv('RECAP_SECTOR'),
              'started_at': time.time(), 'input_hash': key, 'cache_hit': False}
    trace({**record, 'event':'start'})
    try:
        if path and path.exists():
            try:
                cached = json.loads(path.read_text())
                if (cached['key'] == key and 0 <= time.time()-cached['fetched_at'] <= 6*3600
                        and not any(v > today for v in cached['vintage'].values() if v)):
                    record.update(cache_hit=True, vintage=cached['vintage'], status='ok')
                    return cached['rows']
            except (ValueError, KeyError, OSError): pass
        with _slots:
            with _rate:
                wait = max(0, .5-(time.monotonic()-_last))
                time.sleep(wait); _last = time.monotonic()
            # Provider's internal 35+45+55s retry loop otherwise outlives our 45s.
            cp = run(args, timeout=45, text=True, env={**os.environ, 'TUSHARE_NO_RETRY':'1'})
        if cp.returncode != 0 or not cp.stdout.strip():
            record.update(status='failed', returncode=cp.returncode, reason=cp.stderr[-500:])
            return []
        rows = list(csv.DictReader(cp.stdout.splitlines()))
        vintage = {k: max((r.get(k) or '' for r in rows), default='')
                   for k in ('trade_date', 'ann_date', 'end_date')}
        if any(v > today for v in vintage.values() if v):
            record.update(status='failed', reason='future_data', vintage=vintage); return []
        vintage['requested_trade_date'] = params.get('trade_date')
        record.update(status='ok' if rows else 'empty', vintage=vintage)
        if rows and path:
            atomic(path, {'key':key, 'rows':rows, 'vintage':vintage, 'fetched_at':time.time()})
        return rows
    except Exception as e:
        record.update(status='failed', reason=str(e)); return []
    finally:
        record['event'] = 'finish'
        record['elapsed_seconds'] = time.monotonic()-started
        if record.get('status') != 'ok':
            context.failed = True
        trace(record)
