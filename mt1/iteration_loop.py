"""MT14 single run/status/verify/demo entrypoint, isolated durable stages."""
import fcntl
import json
import os
from contextlib import contextmanager
from pathlib import Path
from .data import atomic_json
from .action_loop import now, read
from .timing import digest
from .timing_cli import file_hash
from .iteration_policy import SCHEMA, KINDS, CONTRACT


def init(root, kind):
    root = Path(root).resolve()
    if kind not in KINDS or '.cron_state' in root.parts or 'investment' in root.parts:
        raise ValueError('unsafe_experiment_root_or_kind')
    root.mkdir(parents=True, exist_ok=True)
    marker = root/'.mt14.json'
    if marker.exists():
        if read(marker) != {'schema': SCHEMA, 'kind': kind, 'production': False}:
            raise ValueError('root_identity_changed')
    else:
        if any(root.iterdir()):
            raise ValueError('refuse_adopt_nonempty_root')
        atomic_json(marker, {'schema': SCHEMA, 'kind': kind, 'production': False})
    return root


def child(root, relative):
    root=Path(root).resolve(); p=root/relative
    if not (root/'.mt14.json').is_file() or p.is_symlink() or not p.resolve().is_relative_to(root):
        raise ValueError('outside_isolated_root')
    # Do not follow even an internal symlink: path ownership must be unambiguous.
    if any(x.is_symlink() for x in [p, *p.parents] if x != root and x.is_relative_to(root)):
        raise ValueError('symlink_refused')
    return p


@contextmanager
def locked(root):
    with child(root, '.iteration.lock').open('a') as f:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def event(root, key, value):
    path=child(root, 'events/'+digest(key)+'.json')
    record={'key':key, 'value':value, 'sha256':digest(value)}
    if path.exists():
        if read(path)!=record: raise ValueError('idempotency_key_conflict')
        return record
    atomic_json(path, record)
    return record


def stage(root, run_dir, name, compute):
    p=child(root, str(run_dir.relative_to(root)/name)+'.json')
    if p.exists():
        v=read(p)
        if digest(v['result'])!=v['sha256']:raise ValueError('stage_tampered')
        return v['result']
    result=compute()
    atomic_json(p, {'result':result, 'sha256':digest(result)})
    event(root, str(p.relative_to(root)), {'stage':name,'sha256':digest(result)})
    return result
