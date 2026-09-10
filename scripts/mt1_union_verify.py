#!/usr/bin/env python3
"""Independent SQL anti-join + frozen blob verification, no provider requests."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3


def main():
    p=argparse.ArgumentParser();p.add_argument('--union',required=True);p.add_argument('--output',required=True);a=p.parse_args()
    root=Path(a.union);out=Path(a.output)
    if out.exists():raise SystemExit('output exists')
    report=json.loads((root/'summary.json').read_text())
    sources=json.loads((root/'sources.json').read_text());checked=set()
    for s in sources:
        if s['sha256'] in checked:continue
        with Path(s['snapshot']).open('rb') as f:actual=hashlib.file_digest(f,'sha256').hexdigest()
        if actual!=s['sha256']:raise ValueError('frozen blob mismatch')
        checked.add(actual)
    for ref in report['artifacts']:
        with Path(ref['path']).open('rb') as f:actual=hashlib.file_digest(f,'sha256').hexdigest()
        if actual!=ref['sha256']:raise ValueError('artifact hash mismatch: '+ref['path'])
    db=sqlite3.connect('file:'+str((root/'union.sqlite').resolve())+'?mode=ro',uri=True)
    assert db.execute('PRAGMA quick_check').fetchone()[0]=='ok'
    db.execute('CREATE TEMP TABLE expected(phase TEXT,code TEXT,day TEXT,PRIMARY KEY(code,day))')
    by_day=json.loads((root/'universe.json').read_text())['by_day']
    db.executemany('INSERT INTO expected VALUES(?,?,?)',(('warmup' if day<'2024-01-02' else 'target',code,day) for day,codes in by_day.items() for code in codes))
    fields={'d':['open','high','low','close','vol','amount'],'a':['adj_factor'],'l':['up_limit','down_limit']}
    condition=' OR '.join(f"{alias}.payload IS NULL OR {alias}.conflicts!='[]' OR "+' OR '.join(f"json_extract({alias}.payload,'$.{key}') IS NULL" for key in keys) for alias,keys in fields.items())
    sql="""SELECT e.phase,count(*) FROM expected e
      LEFT JOIN panel d ON d.api='daily' AND d.code=e.code AND d.day=e.day
      LEFT JOIN panel a ON a.api='adj_factor' AND a.code=e.code AND a.day=e.day
      LEFT JOIN panel l ON l.api='stk_limit' AND l.code=e.code AND l.day=e.day
      WHERE """+condition+' GROUP BY e.phase'
    counts=dict(db.execute(sql));line_counts={}
    for phase in ('target','warmup'):
        with gzip.open(root/(phase+'-holes.jsonl.gz'),'rt') as f:
            n=sum(1 for _ in f)
        assert n==counts.get(phase,0)==report['coverage'][phase].get('remaining_holes',0)
        line_counts[phase]=n
    db.close()
    result={'status':'verified_hashes_and_independent_SQL_anti_join_not_PIT_certification',
        'source_files':len(sources),'unique_blobs_verified':len(checked),'remaining_holes':counts,
        'gzip_line_counts':line_counts,'sqlite_quick_check':'ok','PIT_certified':False,'replay_ready':False}
    out.write_text(json.dumps(result,indent=2));print(json.dumps(result))
if __name__=='__main__':main()
