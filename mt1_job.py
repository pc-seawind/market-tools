#!/usr/bin/env python3
"""Inside systemd-run, inherit only data-provider credentials from live worker.

systemd transient units do not inherit the calling agent's environment. Never
put tokens in argv, journal or a temporary plaintext file. No service mutation.
"""
import os
from pathlib import Path
import subprocess
import sys

ALLOWED = {'HOMESPACE_WORKER_ID','TUSHARE_TOKEN','HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY',
           'http_proxy','https_proxy','all_proxy','no_proxy','TUSHARE_INSECURE_SSL'}


def main():
    env=dict(os.environ)
    if not env.get('TUSHARE_TOKEN'):
        pid=subprocess.check_output(['systemctl','--user','show','homespace-worker','-p','MainPID','--value'],text=True,timeout=5).strip()
        if not pid.isdigit() or pid=='0': raise RuntimeError('worker not running; provider credentials unavailable')
        for item in Path('/proc',pid,'environ').read_bytes().split(b'\0'):
            if b'=' not in item: continue
            key,value=item.split(b'=',1)
            if key.decode() in ALLOWED: env[key.decode()]=value.decode()
    args=sys.argv[1:]
    enrich=bool(args and args[0]=='thesis-enrich')
    script=str(Path(__file__).with_name('thesis_enrich_daily.py' if enrich else 'mt1.py'))
    os.execve(sys.executable,[sys.executable,script,*(args[1:] if enrich else args)],env)


if __name__=='__main__': main()
