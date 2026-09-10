"""Whole-universe current snapshot sweep: bounded batches, checkpoints, retries.

Each symbol has a dated outcome, including unavailable data. Does not emit BUY,
modify plans, or publish. Restart the identical command to resume after timeout.
"""
import csv
import fcntl
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from pathlib import Path
from collections import Counter
from .data import HERE, atomic_json
from .store import digest
from .candidates import screen

FIELDS='ts_code,ann_date,end_date,roe,netprofit_yoy,ocfps,eps,debt_to_assets'


def fetch(name, **params):
    # Bypass long-lived financial caches and shared Parquet read-modify-write.
    env={**os.environ,'TUSHARE_NO_CACHE':'1','TUSHARE_NO_PARQUET':'1'}
    cp=subprocess.run([sys.executable,str(HERE/'tushare.py'),name,'--csv',
        *[f'--fields={v}' if k=='fields' else f'{k}={v}' for k,v in params.items()]],
        env=env,capture_output=True,text=True,timeout=25)
    if cp.returncode:raise RuntimeError(f'{name}: exit={cp.returncode}: {cp.stderr[-500:]}')
    return list(csv.DictReader(cp.stdout.splitlines()))


def stamp():return datetime.now(timezone.utc).isoformat()


def outcome(stock, basic, rows, asof):
    if not rows:return {'status':'unavailable','reasons':['empty_financial_response']}
    rows=[r for r in rows if r.get('ts_code')==stock['ts_code']]
    if not rows:return {'status':'unavailable','reasons':['financial_symbol_mismatch']}
    result=screen({'asof':asof,'universe_size':1,'data_version':digest(rows),
        'observations':[{'stock':stock,'daily':basic,'financials':rows}]})
    return {'status':'usable' if result['complete_data_count'] else 'unavailable',
        'reasons':result['excluded'][0]['reasons'] if result['excluded'] else [],
        'shadow_candidate':bool(result['candidates'])}


def sweep(state_dir, asof, batch_size=100, workers=4, attempts=2,
          calls_per_second=3, max_seconds=6600, max_batches=0, provider=fetch):
    date.fromisoformat(asof)
    if date.fromisoformat(asof)>date.today():raise ValueError('future snapshot')
    if not (1<=batch_size<=500 and 1<=workers<=8 and 1<=attempts<=3
            and 0<calls_per_second<=5 and max_seconds>0 and max_batches>=0):
        raise ValueError('invalid bounds')
    root=Path(state_dir)/'sweeps'/asof;root.mkdir(parents=True,exist_ok=True)
    with (root/'lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        def stage(name,params):
            path=root/(name+'.json')
            if path.exists():return json.loads(path.read_text())
            rows=provider(name,**params)
            if not rows:raise ValueError(name+': empty snapshot')
            atomic_json(path,rows);atomic_json(root/(name+'-source.json'),
                {'fetched_at':stamp(),'params':params,'hash':digest(rows),'fresh_api':provider is fetch})
            return rows
        universe=stage('stock_basic',dict(list_status='L',fields='ts_code,name,industry,list_date'))
        if len({s['ts_code'] for s in universe})!=len(universe):raise ValueError('duplicate universe')
        basics=stage('daily_basic',dict(trade_date=asof.replace('-',''),
            fields='ts_code,trade_date,pe_ttm,pb,total_mv,turnover_rate'))
        if any(r.get('trade_date')!=asof.replace('-','') for r in basics):
            raise ValueError('daily snapshot date mismatch')
        basic_by={r['ts_code']:r for r in basics}
        records=root/'symbols';records.mkdir(exist_ok=True)
        states={}
        for stock in universe:
            code=stock['ts_code'];path=records/(code+'.json')
            if path.exists():states[code]=json.loads(path.read_text())
        started=time.monotonic();batch_n=0
        def summary():
            closed=[r for r in states.values() if r['status']=='usable' or r['attempts']>=attempts]
            counts=Counter(r['status'] for r in states.values())
            reasons=Counter(k for r in states.values() if r['status']=='unavailable' for k in r['reasons'])
            result={'asof':asof,'updated_at':stamp(),'universe_size':len(universe),
                'attempted_unique':len(states),'closed':len(closed),
                'usable':counts['usable'],'unavailable':counts['unavailable'],
                'pending':len(universe)-len(closed),'complete':len(closed)==len(universe),
                'current_date_attempt_coverage':len(states)/len(universe),
                'current_date_usable_coverage':counts['usable']/len(universe),
                'unavailable_reasons':dict(reasons),'attempt_limit':attempts,
                'total_requests':sum(r['attempts'] for r in states.values()),
                'data_date_not_wall_date':True,'fresh_api':provider is fetch,
                'classification':'current_snapshot_shadow_not_historical_PIT',
                'symbols_dir':str(records),'universe_hash':digest(universe)}
            atomic_json(root/'summary.json',result);return result
        def one(stock):
            code=stock['ts_code'];prior=states.get(code,{})
            record={'code':code,'asof':asof,'attempts':prior.get('attempts',0)+1,
                'history':prior.get('history',[]),'fetched_at':stamp()}
            try:
                rows=provider('fina_indicator',ts_code=code,fields=FIELDS)
                record.update(outcome(stock,basic_by.get(code,{}),rows,asof))
                atomic_json(root/('financial-'+code+'.json'),rows)
                record['data_hash']=digest(rows)
            except Exception as e:record.update(status='unavailable',reasons=['provider_error'],error=str(e))
            record['history'].append({k:record[k] for k in ('attempts','fetched_at','status','reasons')})
            if 'error' in record:record['history'][-1]['error']=record['error']
            atomic_json(records/(code+'.json'),record)
            return code,record
        summary()
        while time.monotonic()-started<max_seconds and (not max_batches or batch_n<max_batches):
            # Finish the first pass across ALL symbols before retries.
            pending=[s for s in universe if s['ts_code'] not in states or
                (states[s['ts_code']]['status']!='usable' and states[s['ts_code']]['attempts']<attempts)]
            if not pending:break
            pending.sort(key=lambda s:(states.get(s['ts_code'],{}).get('attempts',0),s['ts_code']))
            selected=pending[:batch_size];batch_n+=1;batch_start=stamp()
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures=[]
                for stock in selected:
                    if time.monotonic()-started>=max_seconds:break
                    futures.append(pool.submit(one,stock));time.sleep(1/calls_per_second)
                for f in as_completed(futures):
                    code,r=f.result();states[code]=r
            report=summary()
            with (root/'batches.jsonl').open('a') as out:
                out.write(json.dumps({'started_at':batch_start,'ended_at':stamp(),
                    'codes':[s['ts_code'] for s in selected[:len(futures)]],
                    'summary':report},ensure_ascii=False)+'\n');out.flush();os.fsync(out.fileno())
        return summary()


def launch(state_dir,asof):
    """One independent unit per date; bounded service restarts resume checkpoints."""
    root=Path(state_dir).resolve()/'sweeps'/asof;root.mkdir(parents=True,exist_ok=True)
    with (root/'launch.lock').open('a') as launch_lock:
        fcntl.flock(launch_lock,fcntl.LOCK_EX)
        summary_path=root/'summary.json'
        if summary_path.exists() and json.loads(summary_path.read_text()).get('complete'):
            return {'status':'complete','summary_path':str(summary_path)}
        with (root/'lock').open('a') as process_lock:
            try:fcntl.flock(process_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:return {'status':'already_running','summary_path':str(summary_path)}
        unit='mt1-sweep-'+asof+'-'+digest(str(Path(state_dir).resolve()))[:8]
        active=subprocess.run(['systemctl','--user','is-active',unit],capture_output=True,text=True,timeout=5)
        if active.stdout.strip() in ('active','activating','reloading'):
            return {'status':'already_running','unit':unit,'summary_path':str(summary_path)}
        cmd=['systemd-run','--user','--unit='+unit,'--property=RuntimeMaxSec=7200',
            '--property=Restart=on-failure','--property=RestartSec=60',
            '--property=StartLimitBurst=3','--property=StartLimitIntervalSec=21600',
            '--working-directory='+str(HERE),sys.executable,str(HERE/'mt1_job.py'),
            '--state-dir',str(Path(state_dir).resolve()),'sweep','--asof',asof]
        cp=subprocess.run(cmd,capture_output=True,text=True,timeout=15)
        result={'status':'started' if cp.returncode==0 else 'launch_failed',
            'unit':unit,'command':cmd,'exit_code':cp.returncode,'detail':cp.stderr[-800:],
            'started_at':stamp(),'summary_path':str(summary_path)}
        atomic_json(root/'launch.json',result)
        if cp.returncode:raise RuntimeError('sweep launch failed: '+cp.stderr[-800:])
        return result


def snapshot(state_dir,asof):
    root=Path(state_dir)/'sweeps'/asof
    if not (root/'daily_basic.json').exists():return {'status':'shadow','sweep_status':'awaiting_snapshot','candidates':[]}
    universe=json.loads((root/'stock_basic.json').read_text())
    basics={r['ts_code']:r for r in json.loads((root/'daily_basic.json').read_text())}
    observations=[]
    for s in universe:
        p=root/('financial-'+s['ts_code']+'.json')
        if p.exists():observations.append({'stock':s,'daily':basics.get(s['ts_code'],{}),'financials':json.loads(p.read_text())})
    progress=json.loads((root/'summary.json').read_text()) if (root/'summary.json').exists() else {}
    return screen({'asof':asof,'universe_size':len(universe),'observations':observations,
        'data_version':digest(observations),'coverage_progress':progress})
