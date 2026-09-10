#!/usr/bin/env python3
"""7 bounded read-only requests: JSON/CSV evidence and one historical valuation."""
import csv,hashlib,json,os,socket,subprocess,sys
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));from mt1_job import ALLOWED

def main():
    if socket.gethostname()!='emox-OMEN-30L-Desktop-GT13-0xxx':raise SystemExit('unauthorized host')
    out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=False);env=dict(os.environ)
    pid=subprocess.check_output(['systemctl','--user','show','homespace-worker','-p','MainPID','--value'],text=True,timeout=5).strip()
    for item in Path('/proc',pid,'environ').read_bytes().split(b'\0'):
        if b'=' in item:
            k,v=item.split(b'=',1)
            if k.decode() in ALLOWED:env[k.decode()]=v.decode()
    env.update(TUSHARE_NO_CACHE='1',TUSHARE_NO_PARQUET='1',TUSHARE_NO_RETRY='1')
    cases=[('adj_factor','000001.SZ','20221221'),('adj_factor','000538.SZ','20240102'),('daily','000100.SZ','20260602')]
    tasks=[(api,c,d,mode) for api,c,d in cases for mode in ('json','csv')]+[('daily_basic','600519.SH','20240408','json')]
    results=[]
    for i,(api,c,d,mode) in enumerate(tasks):
        cmd=[sys.executable,str(ROOT/'tushare.py'),api,'ts_code='+c,'trade_date='+d]+(['--csv'] if mode=='csv' else [])
        r=subprocess.run(cmd,env=env,capture_output=True,text=True,timeout=25)
        p=out/f'{i}-{api}-{mode}.txt';p.write_text(r.stdout)
        results.append({'api':api,'code':c,'day':d,'mode':mode,'fetched_at':datetime.now(timezone.utc).isoformat(),'exit':r.returncode,'error':r.stderr[-300:],'path':str(p.resolve()),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
        (out/'summary.json').write_text(json.dumps(results,indent=2))
    print(json.dumps(results))
if __name__=='__main__':main()
