#!/usr/bin/env python3
"""Finite read-only public document capture; no publication, no hidden retries."""
import argparse,hashlib,json,socket,subprocess
from datetime import datetime,timezone
from pathlib import Path

def main():
    if socket.gethostname()!='emox-OMEN-30L-Desktop-GT13-0xxx':raise SystemExit('unauthorized host')
    p=argparse.ArgumentParser();p.add_argument('--manifest',required=True);p.add_argument('--output',required=True);a=p.parse_args()
    tasks=json.loads(Path(a.manifest).read_text())
    if len(tasks)>15:raise ValueError('request budget 15 exceeded')
    out=Path(a.output);out.mkdir(parents=True,exist_ok=False);result=[]
    for task in tasks:
        q=out/(task['id']+'.'+task['kind']);header=out/(task['id']+'.headers')
        cp=subprocess.run(['curl','-sS','-L','--max-redirs','3','--max-time','25','-D',str(header),'-o',str(q),'-w','%{http_code}',task['url']],capture_output=True,text=True,timeout=30)
        entry={**task,'fetched_at':datetime.now(timezone.utc).isoformat(),'exit':cp.returncode,'http':cp.stdout,'error':cp.stderr[-300:]}
        if q.exists():
            raw=q.read_bytes();entry.update(path=str(q.resolve()),sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw))
            if task['kind']=='pdf' and raw.startswith(b'%PDF'):
                txt=q.with_suffix('.txt');r=subprocess.run(['pdftotext','-layout',str(q),str(txt)],capture_output=True,text=True,timeout=30)
                entry.update(text_path=str(txt.resolve()),extract_exit=r.returncode)
                if txt.exists():entry['text_sha256']=hashlib.sha256(txt.read_bytes()).hexdigest()
            elif task['kind']=='pdf':entry['error']='not_pdf'
        result.append(entry);(out/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False))
if __name__=='__main__':main()
