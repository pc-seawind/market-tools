"""Append-only cross-process telemetry; no credentials, no provider side effects."""
from collections import Counter
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import uuid


def emit(kind, **values):
    path = os.getenv('RECAP_OBSERVABILITY')
    if not path: return
    record = dict(kind=kind, at=datetime.now(timezone.utc).isoformat(), monotonic=time.monotonic(),
                  pid=os.getpid(), stage=os.getenv('RECAP_STAGE'), stage_id=os.getenv('RECAP_STAGE_ID'))
    # Stock trace has its own stage='stock'; retain it without colliding with
    # the inherited collector stage or breaking actual sector_picks workers.
    if 'stage' in values: values['detail_stage'] = values.pop('stage')
    record.update(values)
    try:
        with open(path, 'a') as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.write(json.dumps(record, ensure_ascii=False)+'\n')
    except OSError as exc:
        print('recap telemetry unavailable: '+str(exc), file=sys.stderr)


def summary(path):
    stages = {}
    for line in Path(path).read_text().splitlines():
        try: r=json.loads(line)
        except ValueError: continue  # SIGKILL may leave a trailing incomplete record.
        sid=r.get('stage_id')
        if not sid: continue
        stage=stages.setdefault(sid, {'stage':r.get('stage'), 'counts':Counter(), 'status':'unfinished'})
        if r['kind']=='stage_start':stage['started_at']=r['at']
        elif r['kind']=='stage_finish':
            stage.update(finished_at=r['at'],wall_seconds=r['wall_seconds'],status=r['status'],returncode=r.get('returncode'))
        else:
            key=r['kind']
            if key in ('tushare_cache','htsc_cache'):key+=':'+r['outcome']
            if key=='rpc' and r.get('event')=='finish':
                stage['counts']['logical_rpc_finish']+=1
                if r.get('cache_hit'): stage['counts']['logical_cache_hit:'+r.get('cache_layer','unknown')]+=1
                if r.get('cli_started'):stage['counts']['logical_cli_started']+=1
            elif key!='rpc':stage['counts'][key]+=1
    return {'stages':list(stages.values()),
            'status_contract':'stage status reflects process exit only; exit 0 may still mean partial data; inspect checkpoint coverage',
            'counter_contract':'HTTP starts count attempts; CLI/RPC/cache checks are separate, never sum them as remote requests; unfinished has no inferred wall'}


def main():
    if sys.argv[1]=='summary':
        print(json.dumps(summary(sys.argv[2]),ensure_ascii=False,indent=2));return 0
    stage=sys.argv[2];os.environ.update(RECAP_STAGE=stage,RECAP_STAGE_ID=uuid.uuid4().hex)
    start=time.monotonic();rc=None;status='interrupted'
    emit('stage_start')
    if sys.argv[1]=='skip':
        emit('stage_finish',wall_seconds=0,status='skipped_or_reused',returncode=0);return 0
    def terminate(signum, frame):raise SystemExit(128+signum)
    signal.signal(signal.SIGTERM,terminate)
    try:
        rc=subprocess.call(sys.argv[4:]);status='ok' if rc==0 else 'failed';return rc
    finally:emit('stage_finish',wall_seconds=time.monotonic()-start,status=status,returncode=rc)


if __name__=='__main__':sys.exit(main())
