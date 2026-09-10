#!/usr/bin/env python3
"""Bounded sequential manifests, no implicit retries, independent cgroup only.

Each API CLI subprocess is bounded by mt1.sweep.fetch's 25 second timeout.
Only invokes read-only backfill; never publishes or creates schedules.
"""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[1]

def main():
    if socket.gethostname()!='emox-OMEN-30L-Desktop-GT13-0xxx':raise SystemExit('unauthorized host')
    p=argparse.ArgumentParser();p.add_argument('manifests',nargs='+');p.add_argument('--request-budget',type=int,required=True);a=p.parse_args()
    counts=[len(json.loads(Path(m).read_text())['tasks']) for m in a.manifests]
    if not 0<sum(counts)<=a.request_budget or any(n>500 for n in counts):raise SystemExit('request budget exceeded')
    for m,n in zip(a.manifests,counts):
        result=subprocess.run([sys.executable,str(ROOT/'mt1_job.py'),'data-backfill','--input',str(Path(m).resolve()),'--max-requests',str(n)],
            env={**os.environ,'TUSHARE_NO_RETRY':'1'},timeout=3600)
        if result.returncode:raise SystemExit(result.returncode)
if __name__=='__main__':main()
