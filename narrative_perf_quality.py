"""Append-only exception overlay shared by narrative consumers; never edits evidence.

Hashes identify the complete canonical JSON record, not a date/ticker wildcard:
a separately appended corrected observation remains eligible. Invalid ledgers fail
closed (raise), so a malformed exception file cannot silently re-enable bad data.
"""
import hashlib
import json
from pathlib import Path

EXCLUSIONS = Path(__file__).with_name('narrative_perf_exclusions.jsonl')


def record_hash(record):
    return hashlib.sha256(json.dumps(record, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode()).hexdigest()


def filter_perfs(records, exclusions_path=None):
    path = EXCLUSIONS if exclusions_path is None else Path(exclusions_path)
    exclusions = {}
    # The shipped ledger is mandatory; missing/corrupt audit data fails closed.
    for line in path.read_text(encoding='utf-8').splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        if entry['action'] != 'exclude' or not entry['reason']:
            raise ValueError('invalid narrative perf exclusion')
        exclusions[entry['record_sha256']] = entry
    accepted, excluded = [], []
    for row in records:
        entry = exclusions.get(record_hash(row))
        if entry:
            excluded.append(entry)
        else:
            accepted.append(row)
    return accepted, {'excluded_count': len(excluded), 'excluded': excluded,
                      'ledger': str(path), 'scope': 'whole-ledger-before-window-filter'}
