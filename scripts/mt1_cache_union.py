#!/usr/bin/env python3
"""Finite offline union runner; execute under systemd-run with hard limits."""
import argparse
import json
import os
import socket
import sys
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from mt1.cache_union import Union, discover, POLICY, APIS
from mt1.historical import universe,day,price_features
from mt1.evidence import file_ref,inventory


def main():
    if socket.gethostname()!='emox-OMEN-30L-Desktop-GT13-0xxx':raise SystemExit('unauthorized host')
    p=argparse.ArgumentParser();p.add_argument('--output',required=True);a=p.parse_args()
    out=Path(a.output).resolve();out.mkdir(parents=True,exist_ok=False)
    state=ROOT/'.cron_state/mt1'
    caches=sorted({str(Path.home()/'.homespace/cache/market-tools'),os.environ.get('TUSHARE_CACHE_DIR',str(Path.home()/'.homespace/cache/market-tools'))})
    parquets=sorted({str(Path.home()/'.homespace/data/market-tools'),os.environ.get('TUSHARE_PARQUET_DIR',str(Path.home()/'.homespace/data/market-tools'))})
    implementation_refs=[file_ref(ROOT/'mt1/cache_union.py'),file_ref(ROOT/'mt1/historical.py'),file_ref(Path(__file__))]
    entries=discover(caches,parquets,state)
    def save(name,data):
        (out/name).write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False))
    save('discovery.json',{'cache_roots':caches,'parquet_roots':parquets,'slice_root':str(state),
        'implementation_refs':implementation_refs,
        'files':[{'api':e[0],'path':str(e[1]),'kind':e[2],'expected_sha256':e[3]} for e in entries],
        'excluded':[{'path':str(q),'reason':'derived research panel; not attributable raw provider source'} for q in sorted((ROOT/'.cache').glob('*'))],
        'policy':POLICY})
    attributed={str(e[1].resolve()) for e in entries}
    orphan_slices=[]
    for q in sorted((state/'backfill').glob('*/*-rows.json')):
        if str(q.resolve()) in attributed:continue
        raw=json.loads(q.read_text())
        if raw and isinstance(raw,list) and (any(k in raw[0] for k in ('adj_factor','up_limit')) or {'trade_date','open','high','low'}<=set(raw[0])):
            orphan_slices.append({'path':str(q),'reason':'raw panel slice without matching API receipt; cannot infer attribution'})
    save('unattributed-panel-slices.json',orphan_slices)
    if orphan_slices:raise ValueError('unattributed raw panel slices; scope incomplete')
    u=Union(out/'union.sqlite');sources=[]
    for i,entry in enumerate(entries):
        sources.append(u.snapshot(entry,out))
        if i%100==0:print(json.dumps({'sources_done':i+1,'total':len(entries)}),flush=True)
    save('sources.json',sources)
    audit=state/'data-readiness/20260910T180818506822'
    paths=[state/'sweeps/2026-09-10/stock_basic.json',audit/'01-stock_basic.json',audit/'02-stock_basic.json',audit/'11-trade_cal.json']
    master=[]
    for path,status in zip(paths,('L','D','P')):
        master.extend({**r,'list_status':status} for r in json.loads(path.read_text()))
    cal=json.loads(paths[-1].read_text())
    # 2023 is only 242 sessions: calendar insufficiency must remain explicit.
    sessions=sorted(day(r['cal_date']) for r in cal if str(r['is_open'])=='1' and r['cal_date']<='20240930')
    extended=sorted((state/'backfill').glob('*/manifest.json'))
    for manifest in extended:
        tasks=json.loads(manifest.read_text()).get('tasks',[])
        if any(t['api']=='trade_cal' and t['params'].get('start_date','')<='20221201' for t in tasks):
            summary=manifest.with_name('summary.json')
            if summary.exists():
                for r in json.loads(summary.read_text())['results']:
                    if r['task']['api']=='trade_cal' and r.get('artifact'):
                        q=Path(r['artifact']['path'])
                        if file_ref(q)['sha256']!=r['artifact']['sha256']:raise ValueError('calendar hash mismatch')
                        paths.append(q)
                        sessions=sorted(set(sessions)|{day(x['cal_date']) for x in json.loads(q.read_text()) if str(x['is_open'])=='1' and x['cal_date']<='20240930'})
    warm=[d for d in sessions if d<'2024-01-02'][-250:]
    target=[d for d in sessions if '2024-01-02'<=d<='2024-09-30']
    universes=universe(master,warm+target)
    save('universe.json',universes)
    suspension={};suspension_refs=[]
    for summary in sorted((state/'backfill').glob('*/summary.json')):
        for receipt in json.loads(summary.read_text()).get('results',[]):
            task=receipt['task']
            if task['api']!='suspend_d' or not task['params'].get('trade_date') or not receipt.get('artifact'):continue
            q=Path(receipt['artifact']['path'])
            if file_ref(q)['sha256']!=receipt['artifact']['sha256']:raise ValueError('suspension hash mismatch')
            data=json.loads(q.read_text());date=day(task['params']['trade_date'])
            if len(data)>=int(task['params'].get('limit',6000)) or any(day(r['trade_date'])!=date for r in data):raise ValueError('unproven suspension date coverage')
            if date in suspension and suspension[date]!=data:raise ValueError('suspension source conflict')
            suspension[date]=data;suspension_refs.append(receipt['artifact'])
    save('suspension-sources.json',{'by_day':suspension,'refs':suspension_refs,'completeness':'provider date requests below explicit limit; not independent historical version certification'})
    coverage={}
    for name,dates in [('target',target),('warmup',warm)]:
        coverage[name]=u.coverage({d:universes['by_day'][d] for d in dates},out/(name+'-holes.jsonl.gz'),suspension)
    # All available identities, not the old three-symbol panel. A real feature
    # run requires every one of its 250 session rows, including required fields.
    features={};decision='2024-01-02T16:00:00+08:00'
    for code in universes['by_day'].get('2024-01-02',[]):
        ds=(warm+['2024-01-02'])[-250:];panels={a:[] for a in ('daily','adj_factor')};reason=None
        for d in ds:
            for api in panels:
                row,err=u.get(api,code,d)
                if err: reason=f'{api}:{d}:{err}';break
                panels[api].append(row)
            if reason:break
        if reason:features[code]={'status':'blocked','first_gap':reason};continue
        try: features[code]={'status':'features_built_not_PIT_certified','features':price_features(code,decision,ds,panels['daily'],panels['adj_factor'])}
        except ValueError as e:features[code]={'status':'blocked','reason':str(e)}
    save('warmup-features.json',features)
    u.db.execute('CREATE INDEX provenance_key ON provenance(api,code,day)');u.db.commit()
    stats={api:{'keys':u.db.execute('SELECT count(*) FROM panel WHERE api=?',(api,)).fetchone()[0],
        'conflict_keys':u.db.execute("SELECT count(*) FROM panel WHERE api=? AND conflicts!='[]'",(api,)).fetchone()[0]} for api in APIS}
    u.db.close()
    report={'work_id':'work_0754c4a54bd9c615889b','parent_work_id':'work_2584297bb8bd6304e99f',
        'recorded_at':datetime.now(timezone.utc).isoformat(),'policy':POLICY,'source_count':len(sources),
        'source_rows_by_api':{api:sum(s.get('rows',0) for s in sources if s['api']==api) for api in APIS},
        'source_rows':sum(s.get('rows',0) for s in sources),'rejected_rows':sum(s.get('rejected',0) for s in sources),
        'duplicate_rows':sum(s.get('duplicate_keys',0) for s in sources),'conflicting_rows':sum(s.get('conflicting_rows',0) for s in sources),
        'implementation_refs':implementation_refs,'tables':stats,'coverage':coverage,'warmup_calendar_sessions':len(warm),
        'warmup_features_built':sum(v['status']=='features_built_not_PIT_certified' for v in features.values()),
        'master_calendar_refs':[file_ref(q) for q in paths],
        'PIT_certified':False,'replay_ready':False,'metrics':None,'promotion':'shadow',
        'natural_chain':inventory(state),'artifacts':[file_ref(q) for q in sorted(out.iterdir()) if q.is_file()]}
    save('summary.json',report);print(json.dumps({k:report[k] for k in ('source_count','source_rows','coverage','tables','warmup_features_built')}),flush=True)

if __name__=='__main__':main()
