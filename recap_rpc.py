"""Bounded stock RPC fanout, success-only session cache, durable per-API timing."""
import csv
import json
import os
from pathlib import Path
import threading
import time
import fcntl
from contextlib import contextmanager
from datetime import date
from recap_runtime import atomic, digest, run

_slots = threading.BoundedSemaphore(2)
_rate = threading.Lock()
_log_lock = threading.Lock()
_last = 0.0
context = threading.local()


@contextmanager
def single_flight(path):
    """Only identical requests within the existing run cache share a lock."""
    if path is None:
        yield
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def provider_cache_csv(args, api, params):
    """Reuse the existing exact-query cache; never fetch or widen its TTL.

    Only today's positive snapshots qualify. No field projection, date-range
    approximation, financial revision merging or Parquet last-writer policy.
    Misses retain the isolated CLI, its timeout, fallback and rate limiter.
    """
    if (len(args) < 3 or Path(args[1]).resolve() != Path(__file__).with_name('tushare.py')
            or '--no-cache' in args or os.getenv('TUSHARE_NO_CACHE') == '1'
            or not os.getenv('TUSHARE_TOKEN')):
        return None
    import tushare
    query = {k: v for k, v in params.items() if k != 'fields'}
    fields = params.get('fields', '')
    # CLI key=value parsing always produces strings.
    query = {k: str(v) for k, v in query.items()}
    path = Path(tushare.CACHE_DIR)/api/(tushare._cache_key(api, query, fields)+'.json')
    try:
        stamp = path.stat().st_mtime
        if stamp > time.time() or date.fromtimestamp(stamp) != date.today():
            return None
        body = tushare._cache_read(api, query, fields)
        if not body or body.get('code', 0) != 0 or '_neg_ttl' in body:
            return None
        data = body.get('data') or {}
        if not data.get('items') or not data.get('fields'):
            return None
        return tushare.csv_text(data)
    except (OSError, ValueError, TypeError, KeyError):
        return None


def trace(record):
    path = os.getenv('RECAP_RPC_TRACE')
    if path:
        with _log_lock:
            with open(path, 'a') as f: f.write(json.dumps(record, ensure_ascii=False)+'\n')


def csv_rpc(args, api, params):
    started = time.monotonic()
    key = digest([date.today().strftime('%Y%m%d'), args, os.getenv('RECAP_INPUT_HASH', 'interactive-v1')])
    base = os.getenv('RECAP_RPC_CACHE')
    with single_flight(Path(base)/f'{key}.json' if base else None):
        return _csv_rpc(args, api, params, started)


def _csv_rpc(args, api, params, started):
    global _last
    today = date.today().strftime('%Y%m%d')
    key = digest([today, args, os.getenv('RECAP_INPUT_HASH', 'interactive-v1')])
    base = os.getenv('RECAP_RPC_CACHE')
    path = Path(base)/f'{key}.json' if base else None
    record = {'api': api, 'stock': params.get('ts_code'), 'sector': os.getenv('RECAP_SECTOR'),
              'started_at': time.time()-(time.monotonic()-started), 'input_hash': key, 'cache_hit': False,
              'params': params, 'cli_started': False, 'lock_wait_seconds': time.monotonic()-started}
    trace({**record, 'event':'start'})
    try:
        if path and path.exists():
            try:
                cached = json.loads(path.read_text())
                if (cached['key'] == key and 0 <= time.time()-cached['fetched_at'] <= 6*3600
                        and not any(v > today for v in cached['vintage'].values() if v)):
                    record.update(cache_hit=True, cache_layer='recap_run', vintage=cached['vintage'], status='ok')
                    return cached['rows']
            except (ValueError, KeyError, OSError): pass
        output = provider_cache_csv(args, api, params)
        if output is not None:
            record.update(cache_hit=True, cache_layer='tushare_exact', cli_started=False)
        else:
            record.update(cache_layer='cli', cli_started=True)
            with _slots:
                with _rate:
                    wait = max(0, .5-(time.monotonic()-_last))
                    time.sleep(wait); _last = time.monotonic()
                # Internal retries otherwise outlive our 45s.
                cp = run(args, timeout=45, text=True, env={**os.environ, 'TUSHARE_NO_RETRY':'1'})
            if cp.returncode != 0 or not cp.stdout.strip():
                record.update(status='failed', returncode=cp.returncode, reason=cp.stderr[-500:])
                return []
            output = cp.stdout
        rows = list(csv.DictReader(output.splitlines()))
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
