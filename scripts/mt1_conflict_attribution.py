#!/usr/bin/env python3
"""Resolve every conflicting row back to frozen bytes. Offline bounded runner."""
import argparse
from collections import defaultdict,Counter
import hashlib,json,sqlite3,socket,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from mt1.conflict_attribution import classify_values,ratio_diagnostic


def main():
    if socket.gethostname()!='emox-OMEN-30L-Desktop-GT13-0xxx':raise SystemExit('unauthorized host')
    p=argparse.ArgumentParser();p.add_argument('--union',required=True);p.add_argument('--output',required=True);a=p.parse_args()
    out=Path(a.output);out.mkdir(parents=True,exist_ok=False)
    db=sqlite3.connect('file:'+str((Path(a.union)/'union.sqlite').resolve())+'?mode=ro',uri=True)
    conflicts=[];wanted=defaultdict(set);meta={}
    for api,code,day,fs in db.execute("SELECT api,code,day,conflicts FROM panel WHERE conflicts!='[]' ORDER BY api,code,day"):
        refs=list(db.execute('SELECT source,row_number,row_hash FROM provenance WHERE api=? AND code=? AND day=?',(api,code,day)))
        conflicts.append({'api':api,'code':code,'day':day,'fields':json.loads(fs),'refs':refs})
        for sid,n,h in refs:wanted[sid].add(n)
    decoded={}
    for sid,positions in wanted.items():
        path,sha,blob=db.execute('SELECT path,sha256,snapshot FROM sources WHERE id=?',(sid,)).fetchone()
        with open(blob,'rb') as f:
            if hashlib.file_digest(f,'sha256').hexdigest()!=sha:raise ValueError('source hash mismatch')
        meta[sid]={'path':path,'sha256':sha,'snapshot':blob}
        if path.endswith('.parquet'):
            import pyarrow.parquet as pq
            offset=0
            for batch in pq.ParquetFile(blob).iter_batches(batch_size=10000):
                needed=[n for n in positions if offset<=n<offset+batch.num_rows]
                if needed:
                    rows=batch.to_pylist()
                    for n in needed:decoded[sid,n]=rows[n-offset]
                offset+=batch.num_rows
        else:
            x=json.loads(Path(blob).read_text())
            for n in positions:
                decoded[sid,n]=x[n] if isinstance(x,list) else dict(zip(x['data']['fields'],x['data']['items'][n]))
    groups=defaultdict(list)
    for c in conflicts:
        values=[]
        for sid,n,sha in c.pop('refs'):
            r=decoded[sid,n]
            if hashlib.sha256(json.dumps(r,sort_keys=True,default=str).encode()).hexdigest()!=sha:raise ValueError('row hash mismatch')
            values.append({'source_id':sid,'row_number':n,'row_hash':sha,'row':r})
        c['sources']=values
        c['differences']={f:classify_values([v['row'][f] for v in values if v['row'].get(f) is not None]) for f in c['fields']}
        if c['api']=='adj_factor':
            old=[v['row']['adj_factor'] for v in values if meta[v['source_id']]['path'].endswith('.parquet')]
            new=[v['row']['adj_factor'] for v in values if '/backfill/' in meta[v['source_id']]['path']]
            if old and new:groups[c['code']].append((old[0],new[0]))
    (out/'conflicts.json').write_text(json.dumps(conflicts,ensure_ascii=False,indent=2))
    (out/'sources.json').write_text(json.dumps(meta,indent=2))
    (out/'ratios.json').write_text(json.dumps({c:ratio_diagnostic(v) for c,v in groups.items()},indent=2))
    summary={'keys':len(conflicts),'by_api':dict(Counter(c['api'] for c in conflicts)),
        'factor_classification':dict(Counter(c['differences']['adj_factor']['kind'] for c in conflicts if c['api']=='adj_factor')),
        'unchanged_quarantine':True,'normalization_applied':False,'PIT_certified':False}
    (out/'summary.json').write_text(json.dumps(summary,indent=2));print(summary)
if __name__=='__main__':main()
