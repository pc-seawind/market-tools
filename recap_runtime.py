"""Recap reliability primitives. No decisions, ledger writes or historical backfill."""
import csv
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parent


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def code_hash():
    # Include all local data-provider/strategy code and static configuration.
    paths = sorted([*ROOT.glob('*.py'), *ROOT.glob('*.yaml'), ROOT/'evening_recap_data.sh'])
    return digest([(p.name, hashlib.sha256(p.read_bytes()).hexdigest()) for p in paths])


def atomic(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.recap-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(value, f, ensure_ascii=False, indent=2); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)


def kill_tree(pid):
    """Freeze then reap our descendants, including nested setsid RPC workers.

    Enumerate every task: subprocesses can be created from a pool thread, not
    just the leader. Stopping ancestors first prevents races with new children.
    """
    try: os.kill(pid, signal.SIGSTOP)
    except ProcessLookupError: return
    children = set()
    for task in Path(f'/proc/{pid}/task').glob('*/children'):
        try: children.update(int(c) for c in task.read_text().split())
        except (OSError, ValueError): pass
    for child in children: kill_tree(child)
    try: os.kill(pid, signal.SIGKILL)
    except ProcessLookupError: pass


def run(args, *, timeout, **kwargs):
    """A deadline kills the entire child process group, including RPC grandchildren."""
    kwargs.pop('capture_output', None)
    with subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          start_new_session=True, **kwargs) as p:
        try:
            stdout, stderr = p.communicate(timeout=timeout)
        except BaseException:
            kill_tree(p.pid)
            try: os.killpg(p.pid, signal.SIGKILL)
            except ProcessLookupError: pass
            p.communicate()
            raise
        return subprocess.CompletedProcess(args, p.returncode, stdout, stderr)


def source_grade(value):
    text = json.dumps(value, ensure_ascii=False).lower()
    if any(t in text for t in ('neutral', 'stub', 'proxy', 'deprecated')):
        return 'degraded_proxy_neutral_stub'
    if not isinstance(value, dict) or not value.get('data_quality'):
        return 'unknown'
    return 'provider_reported'  # not a certification of complete company research


def view(value, expected):
    """Retain every failure separately; only date-validated stock rows are usable.

    Legacy score rows have no per-row vintage: expose them as diagnostics, not
    full facts. A good index freshness probe never certifies unrelated RPC data.
    """
    from copy import deepcopy
    out = deepcopy(value); meta = out.setdefault('meta', {})
    target = expected.replace('-', '')
    envelope_ok = meta.get('fresh') is True and str(meta.get('trade_date', '')).replace('-', '') == target
    good, failed, diagnostics = {}, {}, {}
    for concept, item in out.get('picks', {}).items():
        reason = item.get('error') if isinstance(item, dict) else 'invalid_pick_shape'
        if not reason and not envelope_ok: reason = 'recap_stale_or_unknown_trade_date'
        if not reason and item.get('coverage', {}).get('missing'): reason = 'incomplete_stock_inputs'
        rows = item.get('evaluations', []) if isinstance(item, dict) else []
        valid, rejected = [], []
        for row in rows:
            stock = row.get('stock', {})
            if str(stock.get('trade_date', '')).replace('-', '') != target:
                rejected.append({'code': stock.get('code'), 'reason': 'stock_stale_future_or_unknown'})
            else: valid.append(row)
        grade = source_grade(item.get('sector_score', {})) if isinstance(item, dict) else 'unknown'
        diagnostics[concept] = {'source_grade': grade, 'valid_stocks': len(valid), 'rejected': rejected}
        if not reason and not valid: reason = 'no_date_validated_stock_rows'
        if reason:
            failed[concept] = {'reason': reason, 'owner': 'code:recap-collector',
                               'next_action': 'retry_failed_inputs_within_same_session_budget; historical gaps stay archived'}
        else:
            item['evaluations'] = valid
            item['source_grade'] = grade
            # Machine candidates only: degraded score is explicitly NOT a full fact.
            good[concept] = item
            if rejected:
                failed[concept] = {'reason': 'some_stock_rows_rejected', 'rows': rejected,
                                   'owner': 'code:recap-collector', 'next_action': 'refresh_invalid_vintage'}
    scores = out.get('scores', [])
    score_coverage = [{'concept': s.get('concept'), 'source_grade': source_grade(s),
                       'vintage': s.get('trade_date'),
                       'fact_eligible': envelope_ok and str(s.get('trade_date', '')).replace('-', '') == target
                           and source_grade(s) == 'provider_reported'} for s in scores]
    coverage = {'expected_trade_date': expected, 'envelope_fresh': envelope_ok,
                'selected': meta.get('n_picks_selected', len(out.get('picks', {}))),
                'usable_sectors': list(good), 'failed': failed, 'diagnostics': diagnostics,
                'scores': score_coverage, 'complete_facts': sum(r['fact_eligible'] for r in score_coverage),
                'status': 'partial' if failed or meta.get('errors') or not envelope_ok else 'complete',
                'original_errors': list(meta.get('errors', []))}
    out['picks'] = good
    out['coverage'] = coverage
    return out


def reusable(previous, signature, date, now=None):
    from datetime import datetime
    now = now or datetime.now()
    meta = previous.get('meta', {})
    try:
        age = (now - datetime.fromisoformat(meta.get('snapshot_created_at', meta['generated_at']))).total_seconds()
        return (meta.get('input_hash') == signature and meta.get('date') == date
                and meta.get('fresh') is True and 0 <= age <= 6*3600)
    except (KeyError, ValueError, TypeError): return False
