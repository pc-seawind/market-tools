"""Default recap entry: at most two collector invocations, ONE shared budget.

No scheduler, no publication, no historical backfill. Terminal gaps have an
owner/inspection command instead of promising a retry that nobody schedules.
"""
import json
import math
import os
from pathlib import Path
import subprocess
import time
from recap_runtime import run, view
from .data import HERE, atomic_json


def collect(path, expected, today, receipt_path):
    if expected != today:
        raise ValueError('recap recovery refuses historical recollection')
    path = Path(path)
    budget = min(2280, int(os.environ.get('EVENING_RECAP_BUDGET_SECONDS', '2280')))
    if budget <= 0: raise ValueError('recap budget must be positive')
    start = time.monotonic(); deadline = start + budget
    receipt = {'entry': 'mt1_job.py run evening --collect', 'budget_seconds': budget,
               'attempts': [], 'owner': 'code:recap-collector', 'status': 'running',
               'checkpoint': str(path)}

    def read():
        try: return json.loads(path.read_text())
        except (OSError, ValueError): return None

    def save(status):
        receipt.update(status=status, elapsed_seconds=time.monotonic()-start)
        receipt['automatic_retry_pending'] = status == 'running'
        receipt['next_action'] = ('none' if status == 'complete' else
            'code owner: inspect checkpoint and RPC trace; automatic recovery ended for this run; '
            'do not reset attempt counts or launch a new budget without an explicit repair decision')
        receipt['inspect_command'] = ['tail', '-n', '50', str(path)+'.rpc.jsonl']
        atomic_json(receipt_path, receipt)

    value = read()
    status = 'attempt_limit'
    for round_no in (1, 2):
        if value:
            coverage = view(value, expected)['coverage']
            if coverage['status'] == 'complete':
                status = 'complete'; break
            failed = coverage['failed']
            counts = value.get('meta', {}).get('attempt_counts', {})
            if failed and all(counts.get(name, 0) >= 2 for name in failed):
                status = 'attempt_limit'; break
        left = math.floor(deadline-time.monotonic())
        if left < 1:
            status = 'budget_exhausted'; break
        attempt = {'round':round_no, 'timeout_seconds':left, 'collector_budget_seconds':left}
        receipt['attempts'].append(attempt); save('running')
        try:
            cp = run([str(HERE/'evening_recap_data.sh'), '--out', str(path)],
                     timeout=left, text=True,
                     env={**os.environ, 'EVENING_RECAP_BUDGET_SECONDS': str(left)})
            attempt.update(returncode=cp.returncode, stderr=cp.stderr[-1000:])
        except subprocess.TimeoutExpired:
            attempt['error'] = 'shared_budget_exhausted'
            value = read(); status = 'budget_exhausted'; break
        except OSError as exc:
            attempt['error'] = str(exc)
            value = read(); status = 'collector_unavailable'; break
        value = read()
        if cp.returncode == 75:
            status = 'already_running'; break
        if not value:
            status = 'checkpoint_unavailable'; break
        coverage = view(value, expected)['coverage']
        attempt['coverage'] = coverage
        if coverage['status'] == 'complete':
            status = 'complete'; break
        meta = value.get('meta', {})
        # A second pass must reuse this exact successful input/checkpoint, not
        # freshen an unknown or stale artifact into supposed historical truth.
        if (not meta.get('input_hash') or meta.get('date') != today
                or not coverage['envelope_fresh']):
            status = 'invalid_checkpoint'; break
        save('running')
    save(status)
    return value, receipt
