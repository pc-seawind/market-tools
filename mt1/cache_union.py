"""Offline, fail-closed market panel union. Hashes prove bytes, NOT PIT truth.

Every raw source is snapshotted before parsing. Conflicting non-null fields
quarantine the entire security/date/API key; no source or timestamp wins.
Missing identity is rejected (cache filename hashes cannot identify securities).
"""
import hashlib
import json
import sqlite3
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path
from .historical import day

APIS = ('daily', 'adj_factor', 'stk_limit')
FIELDS = {
    'daily': ('open', 'high', 'low', 'close', 'vol', 'amount', 'pre_close', 'pct_chg', 'change'),
    'adj_factor': ('adj_factor',),
    'stk_limit': ('pre_close', 'up_limit', 'down_limit'),
}
REQUIRED = {'daily': ('open','high','low','close','vol','amount'),
            'adj_factor': ('adj_factor',), 'stk_limit': ('up_limit','down_limit')}
POLICY = 'non-null field union; unequal overlapping values quarantine whole key; no last-writer wins; missing identity rejected; no PIT inference'


def canonical(value):
    if value is None or str(value).strip().lower() in ('', 'none', 'null', 'nan'):
        return None
    try:
        n = Decimal(str(value))
        if not n.is_finite(): raise ValueError('non-finite number')
        return str(n.normalize()) if n else '0'
    except InvalidOperation as e:
        raise ValueError('not numeric') from e


def normalize(api, row):
    code = row.get('ts_code')
    if not isinstance(code, str) or not code.endswith(('.SH','.SZ','.BJ')) or not code[:6].isdigit() or len(code)!=9:
        raise ValueError('missing_or_invalid_security_identity')
    date = day(row['trade_date'])
    values = {k: canonical(row.get(k)) for k in FIELDS[api]}
    return code, date, {k:v for k,v in values.items() if v is not None}


def merge(left, right):
    conflict = sorted(k for k in left.keys() & right.keys() if left[k] != right[k])
    # Preserve original value only to detect further conflicts; never serve a
    # quarantined key. All alternatives remain in immutable source snapshots.
    return {**right, **left}, conflict


def discover(cache_roots, parquet_roots, state):
    """Explicit source types only; generated audits are not ingested recursively."""
    found = {}
    for root in cache_roots:
        for api in APIS:
            for p in sorted((Path(root)/api).glob('*.json')):
                found[str(p.resolve())] = (api, p, 'response', None)
    for root in parquet_roots:
        for api in APIS:
            for p in sorted((Path(root)/api).glob('*.parquet')):
                found[str(p.resolve())] = (api, p, 'parquet', None)
    for p in sorted((Path(state)/'backfill').glob('*/*.json')):
        if p.name.endswith('-rows.json'): continue
        body = json.loads(p.read_text())
        receipts = body.get('results', []) if isinstance(body,dict) else []
        if isinstance(body,dict) and 'task' in body: receipts = [body]
        for r in receipts:
            api = r.get('task',{}).get('api'); ref = r.get('artifact')
            if api in APIS and ref:
                q = Path(ref['path'])
                found[str(q.resolve())] = (api,q,'rows',ref['sha256'])
    for p in sorted((Path(state)/'data-readiness').glob('*/audit.json')):
        for r in json.loads(p.read_text()).get('probes',[]):
            if r['api'] in APIS and r.get('artifact'):
                ref=r['artifact'];q=Path(ref['path'])
                found[str(q.resolve())]=(r['api'],q,'rows',ref['sha256'])
    return [found[k] for k in sorted(found)]


class Union:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS panel(api TEXT, code TEXT, day TEXT, payload TEXT,
          conflicts TEXT, PRIMARY KEY(api,code,day));
        CREATE TABLE IF NOT EXISTS provenance(api TEXT, code TEXT, day TEXT, source INTEGER,
          row_number INTEGER, row_hash TEXT);
        CREATE TABLE IF NOT EXISTS sources(id INTEGER PRIMARY KEY, api TEXT, path TEXT,
          sha256 TEXT, snapshot TEXT, rows INTEGER, rejected INTEGER, status TEXT);
        CREATE TABLE IF NOT EXISTS rejects(source INTEGER, row_number INTEGER, reason TEXT);
        ''')

    def ingest(self, api, rows, source):
        counts=Counter()
        for i,row in enumerate(rows):
            counts['rows']+=1
            try: code,date,values=normalize(api,row)
            except (KeyError,ValueError,TypeError) as e:
                counts['rejected']+=1
                self.db.execute('INSERT INTO rejects VALUES(?,?,?)',(source,i,str(e)))
                continue
            old=self.db.execute('SELECT payload,conflicts FROM panel WHERE api=? AND code=? AND day=?',(api,code,date)).fetchone()
            conflicts=[]
            if old:
                counts['duplicate_keys']+=1
                values,conflicts=merge(json.loads(old[0]),values)
                if conflicts: counts['conflicting_rows']+=1
                conflicts=sorted(set(conflicts+json.loads(old[1])))
            payload=json.dumps(values,sort_keys=True)
            self.db.execute('INSERT OR REPLACE INTO panel VALUES(?,?,?,?,?)',(api,code,date,payload,json.dumps(conflicts)))
            raw=json.dumps(row,sort_keys=True,default=str)
            self.db.execute('INSERT INTO provenance VALUES(?,?,?,?,?,?)',(api,code,date,source,i,hashlib.sha256(raw.encode()).hexdigest()))
        return dict(counts)

    def get(self, api, code, date):
        r=self.db.execute('SELECT payload,conflicts FROM panel WHERE api=? AND code=? AND day=?',(api,code,day(date))).fetchone()
        if not r: return None,'missing_row'
        if json.loads(r[1]): return None,'source_conflict'
        values=json.loads(r[0])
        if any(k not in values for k in REQUIRED[api]): return None,'missing_required_fields'
        return {'ts_code':code,'trade_date':day(date),**values},None

    def snapshot(self, entry, out):
        api,path,kind,expected=entry
        raw=path.read_bytes();sha=hashlib.sha256(raw).hexdigest()
        if expected and sha!=expected: raise ValueError('source hash mismatch: '+str(path))
        blob=Path(out)/'sources'/sha
        blob.parent.mkdir(exist_ok=True)
        if not blob.exists():
            with blob.open('xb') as f: f.write(raw)
        if kind=='parquet':
            import pyarrow.parquet as pq
            def batches():
                for batch in pq.ParquetFile(blob).iter_batches(batch_size=10000):
                    yield from batch.to_pylist()
            rows=batches()
        else:
            body=json.loads(raw)
            if kind=='rows': rows=body
            else:
                if 'code' not in body:
                    raise ValueError('unrecognized provider response: '+str(path))
                if body.get('code')!=0:
                    rows=[]
                else:
                    data=body.get('data') or {}
                    rows=[dict(zip(data.get('fields',[]),r)) for r in data.get('items') or []]
        cur=self.db.execute('INSERT INTO sources(api,path,sha256,snapshot,status) VALUES(?,?,?,?,?)',
                            (api,str(path.resolve()),sha,str(blob.resolve()),'parsing'))
        sid=cur.lastrowid
        counts=self.ingest(api,rows,sid)
        status = ('provider_error' if kind=='response' and body.get('code')!=0 else
                  'empty_response' if not counts.get('rows') else 'parsed')
        self.db.execute('UPDATE sources SET rows=?,rejected=?,status=? WHERE id=?',
                        (counts.get('rows',0),counts.get('rejected',0),status,sid))
        self.db.commit()
        return {'id':sid,'api':api,'path':str(path.resolve()),'sha256':sha,'snapshot':str(blob.resolve()),'status':status,**counts}

    def coverage(self, by_day, output, suspension_days=None):
        """Stream full anti-join incl. conflicted/incomplete fields; no 0-fill."""
        import gzip
        counts=Counter();per_api=Counter();suspension_days=suspension_days or {}
        with gzip.open(output,'wt') as f:
            for date,codes in sorted(by_day.items()):
                for code in codes:
                    counts['expected_symbol_sessions']+=1
                    missing={}
                    for api in APIS:
                        _,reason=self.get(api,code,date)
                        if reason: missing[api]=reason;per_api[api+':'+reason]+=1
                    if missing:
                        counts['remaining_holes']+=1
                        events=[r for r in suspension_days.get(date,[]) if r.get('ts_code')==code]
                        classification=('suspension_record_needs_review' if events else 'unexplained_missing_row' if date in suspension_days else 'suspension_coverage_unknown')
                        counts[classification]+=1
                        f.write(json.dumps({'code':code,'day':date,'missing':missing,'classification':classification,'suspension_events':events})+'\n')
                    else: counts['complete_symbol_sessions']+=1
        return {**counts,'by_api_reason':dict(per_api)}
