"""SQLite append-only event ledger, optimistic versions and serialized writers."""
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=30, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS events (
          seq INTEGER PRIMARY KEY, entity TEXT NOT NULL, version INTEGER NOT NULL,
          request_id TEXT NOT NULL UNIQUE, request_hash TEXT NOT NULL,
          reason TEXT NOT NULL, before_json TEXT, after_json TEXT NOT NULL,
          created_at TEXT NOT NULL, UNIQUE(entity,version));
        CREATE TRIGGER IF NOT EXISTS immutable_update BEFORE UPDATE ON events
          BEGIN SELECT RAISE(ABORT,'append-only'); END;
        CREATE TRIGGER IF NOT EXISTS immutable_delete BEFORE DELETE ON events
          BEGIN SELECT RAISE(ABORT,'append-only'); END;
        ''')

    def close(self):
        self.db.close()

    def latest(self, entity):
        row = self.db.execute('SELECT after_json FROM events WHERE entity=? ORDER BY version DESC LIMIT 1', (entity,)).fetchone()
        return json.loads(row[0]) if row else None

    def all(self, prefix='plan:'):
        rows = self.db.execute('SELECT after_json FROM events e WHERE entity LIKE ? AND version=(SELECT MAX(version) FROM events WHERE entity=e.entity)', (prefix+'%',))
        values = [json.loads(r[0]) for r in rows]
        from .scope import filter_plans
        return filter_plans(values) if prefix == 'plan:' else values

    def apply(self, entity, expected, request_id, payload, reason, reducer):
        if not reason.strip() or not request_id:
            raise ValueError('reason and request_id required')
        if entity.startswith('plan:'):
            from .scope import load, filter_plans
            scope = load()
            proposed = {**(self.latest(entity) or {}), **payload}
            if scope is not None and not filter_plans([proposed],scope):
                raise ValueError('plan_outside_tracking_scope')
        fingerprint = digest([entity, expected, payload, reason])
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row = self.db.execute('SELECT request_hash,after_json FROM events WHERE request_id=?', (request_id,)).fetchone()
            if row:
                if row[0] != fingerprint:
                    raise ValueError('idempotency key reused with different content')
                self.db.execute('COMMIT')
                return json.loads(row[1])
            before = self.latest(entity)
            version = before['version'] if before else 0
            if version != expected:
                raise ValueError(f'version conflict: expected {expected}, actual {version}')
            after = reducer(before, payload)
            after['version'] = version + 1
            self.db.execute('INSERT INTO events(entity,version,request_id,request_hash,reason,before_json,after_json,created_at) VALUES(?,?,?,?,?,?,?,?)',
                            (entity, version+1, request_id, fingerprint, reason,
                             encoded(before) if before else None, encoded(after), datetime.now(timezone.utc).isoformat()))
            self.db.execute('COMMIT')
            return after
        except BaseException:
            self.db.execute('ROLLBACK')
            raise
