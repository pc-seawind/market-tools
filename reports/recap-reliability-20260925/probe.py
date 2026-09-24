import sys,os,time,json,subprocess,traceback
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT));os.chdir(ROOT)
out=Path(sys.argv[1]); original=subprocess.run

def run(args,*a,**kw):
 start=time.monotonic(); rec={'args':args,'caller':traceback.extract_stack(limit=3)[-2].name,'started':time.time()}
 try:
  cp=original(args,*a,**kw);rec.update(returncode=cp.returncode,stderr=str(cp.stderr)[-300:]);return cp
 except Exception as e: rec['error']=str(e);raise
 finally:
  rec['elapsed']=time.monotonic()-start
  with out.open('a') as f:f.write(json.dumps(rec,ensure_ascii=False)+'\n')
subprocess.run=run
import sector_picks
sector_picks._append_history=lambda *a:None
s=time.monotonic(); result=sector_picks.sector_picks('存储芯片 (HBM/DDR/NAND)')
out.with_suffix('.result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
print('elapsed',time.monotonic()-s)
