#!/usr/bin/env python3
"""Bound each test module; retain node IDs and last active test on timeout."""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

HERE=Path(__file__).resolve().parents[1]

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True)
    ap.add_argument('--timeout',type=int,default=20)
    ap.add_argument('--nodes',type=Path,help='Optional JSON array of precise test node IDs')
    ap.add_argument('--trace-after',type=int,default=0,help='Optional faulthandler diagnostic delay')
    args=ap.parse_args()
    out=args.out.resolve();out.mkdir(parents=True,exist_ok=False)
    env={**os.environ,'PYTHONPATH':str(HERE)+os.pathsep+os.environ.get('PYTHONPATH','')}
    rows=[]
    targets=json.loads(args.nodes.read_text()) if args.nodes else [str(p.relative_to(HERE)) for p in sorted((HERE/'tests').glob('test_*.py'))]
    for index,target in enumerate(targets):
        cmd=[sys.executable,'-m','pytest','-vv','--tb=short','-o',f'faulthandler_timeout={args.trace_after}',target]
        start=time.monotonic();at=dt.datetime.now(dt.timezone.utc).isoformat()
        log=out/((f'node-{index:03d}' if args.nodes else Path(target).stem)+'.txt')
        with log.open('w') as f:
            p=subprocess.Popen(cmd,cwd=HERE,env=env,stdout=f,stderr=subprocess.STDOUT,start_new_session=True)
            timed_out=False
            try:rc=p.wait(timeout=args.timeout)
            except subprocess.TimeoutExpired:
                timed_out=True;os.killpg(p.pid,signal.SIGTERM)
                try:p.wait(timeout=3)
                except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait()
                rc=124
        text=log.read_text();nodes=[line for line in text.splitlines() if line.startswith('tests/')]
        row=dict(module=target,argv=cmd,started_at=at,elapsed_seconds=round(time.monotonic()-start,3),
                 returncode=rc,timed_out=timed_out,timeout_seconds=args.timeout,
                 last_node=nodes[-1] if nodes else None,log=str(log))
        rows.append(row)
        (out/'summary.json').write_text(json.dumps(rows,indent=2)+'\n')
        print(json.dumps(row),flush=True)
    return int(any(x['returncode'] for x in rows))

if __name__=='__main__':sys.exit(main())
