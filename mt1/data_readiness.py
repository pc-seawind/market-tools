"""Reproducible cache inventory and bounded LIVE permission/data probes.

Evidence only. Never turns availability into point-in-time correctness or returns.
"""
import json
from pathlib import Path
from datetime import datetime,timezone
from .data import HERE,atomic_json
from .sweep import fetch
from .evidence import file_ref

PROBES=[
 ('anns_d',{'ts_code':'600519.SH','start_date':'20240301','end_date':'20240430','fields':'ts_code,ann_date,title,url,rec_time'}),
 ('fina_indicator_vip',{'period':'20260630','fields':'ts_code,ann_date,end_date,roe,netprofit_yoy,ocfps,eps,debt_to_assets'}),
 ('stock_basic',{'list_status':'D','fields':'ts_code,name,list_date,delist_date'}),
 ('stock_basic',{'list_status':'P','fields':'ts_code,name,list_date,delist_date'}),
 ('index_member_all',{'ts_code':'600519.SH','is_new':'N','fields':'l1_code,l1_name,l2_code,l2_name,l3_code,l3_name,ts_code,in_date,out_date,is_new'}),
 ('daily',{'ts_code':'600519.SH','start_date':'20240101','end_date':'20240930','fields':'ts_code,trade_date,open,high,low,close,vol,amount'}),
 ('adj_factor',{'ts_code':'600519.SH','start_date':'20240101','end_date':'20240930','fields':'ts_code,trade_date,adj_factor'}),
 ('suspend_d',{'trade_date':'20240930','fields':'ts_code,trade_date,suspend_timing,suspend_type'}),
 ('stk_limit',{'ts_code':'600519.SH','start_date':'20240101','end_date':'20240930','fields':'ts_code,trade_date,pre_close,up_limit,down_limit'}),
 ('index_daily',{'ts_code':'000300.SH','start_date':'20240101','end_date':'20240930','fields':'ts_code,trade_date,open,close'}),
 ('fina_indicator',{'ts_code':'600519.SH','start_date':'20230101','end_date':'20240930','fields':'ts_code,ann_date,end_date,roe,netprofit_yoy,ocfps,eps,debt_to_assets'}),
 ('income',{'ts_code':'600519.SH','start_date':'20230101','end_date':'20240930','fields':'ts_code,ann_date,f_ann_date,end_date,report_type,update_flag,n_income,total_revenue'}),
 ('trade_cal',{'exchange':'SSE','start_date':'20230101','end_date':'20241231','fields':'cal_date,is_open'})]


def inventory():
    cache=Path.home()/'.homespace/cache/market-tools'
    parquet=Path.home()/'.homespace/data/market-tools'
    results={}
    for name in sorted({n for n,p in PROBES}|{'ths_member','index_member','moneyflow'}):
        files=list((cache/name).glob('*.json'))
        p=parquet/name/(name+'.parquet')
        result={'json_files':len(files),'json_bytes':sum(f.stat().st_size for f in files),
                'parquet_exists':p.exists()}
        if p.exists():
            try:
                import pyarrow.parquet as pq
                meta=pq.ParquetFile(p)
                result.update(parquet_path=str(p),rows=meta.metadata.num_rows,fields=meta.schema.names)
                if name=='fina_indicator':result['PIT_warning']='cache_parquet KEYS=(ts_code,end_date): revisions overwrite; ann_date presence alone cannot reconstruct original versions'
            except Exception as e:result['metadata_error']=str(e)
        results[name]=result
    history=HERE/'sector_picks_history.jsonl'
    missing={k:0 for k in ('channel','action','method_version','available_at','decision_at')};n=0;dates=[]
    if history.exists():
        for line in history.read_text().splitlines():
            try:r=json.loads(line)
            except ValueError:continue
            n+=1
            if r.get('ts'):dates.append(r['ts'])
            for k in missing:
                if not r.get(k):missing[k]+=1
    return {'datasets':results,'channel_history':{'file':file_ref(history),'rows':n,
        'first':min(dates) if dates else None,'last':max(dates) if dates else None,
        'missing_fields':missing,'exact_reconstruction':False},
        'score_snapshots':[file_ref(p) for p in sorted((HERE/'data/score_snapshots').glob('*.json'))],
        'backtest_dataset':file_ref(HERE/'backtest_dataset.jsonl')}


def audit(state_dir,provider=fetch):
    out=Path(state_dir)/'data-readiness'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')
    out.mkdir(parents=True)
    result={'fetched_at':datetime.now(timezone.utc).isoformat(),'inventory':inventory(),
        'fresh_api':provider is fetch,'probes':[], 'exact_backtest_status':'incomplete','metrics':None,'new_factor':'shadow'}
    atomic_json(out/'audit.json',result)
    for i,(name,params) in enumerate(PROBES):
        item={'api':name,'params':params,'command':['python3','mt1_job.py','data-audit'],
              'started_at':datetime.now(timezone.utc).isoformat()}
        try:
            rows=provider(name,**params);p=out/f'{i:02}-{name}.json';atomic_json(p,rows)
            item.update(status='available' if rows else 'available_empty',rows=len(rows),
                artifact=file_ref(p),fields=list(rows[0]) if rows else [],sample=rows[:1])
        except Exception as e:item.update(status='failed',error=str(e))
        result['probes'].append(item);atomic_json(out/'audit.json',result)
    return {'out_dir':str(out),'audit':str(out/'audit.json'),'status':'incomplete','metrics':None}


def _backfill(state_dir,manifest,max_requests=100,provider=fetch):
    """Finite read-only request list; immutable responses and per-task checkpoints."""
    from .store import digest
    if not 1<=max_requests<=500:raise ValueError('max_requests must be 1..500')
    tasks=manifest['tasks'];allowed={n for n,p in PROBES}|{'daily_basic','dividend'}
    if any(t['api'] not in allowed or not t.get('id') for t in tasks):raise ValueError('unsupported task')
    if len({t['id'] for t in tasks})!=len(tasks):raise ValueError('duplicate task ids')
    root=Path(state_dir)/'backfill'/digest(manifest)[:20];root.mkdir(parents=True,exist_ok=True)
    atomic_json(root/'manifest.json',manifest);results=[];used=0
    for task in tasks:
        path=root/(digest(task)[:20]+'.json')
        if path.exists():
            old=json.loads(path.read_text())
            if old['status']!='failed' or old['attempts']>=2:
                results.append(old);continue
        else:old={}
        if used>=max_requests:
            results.append(old or {'task':task,'status':'pending','attempts':0});continue
        used+=1;item={'task':task,'attempts':old.get('attempts',0)+1,
            'fetched_at':datetime.now(timezone.utc).isoformat(),'fresh_api':provider is fetch}
        try:
            rows=provider(task['api'],**task['params'])
            data=root/(digest([task,item['fetched_at']])[:24]+'-rows.json');atomic_json(data,rows)
            item.update(status='available' if rows else 'available_empty',rows=len(rows),artifact=file_ref(data))
        except Exception as e:item.update(status='failed',error=str(e))
        item['history']=old.get('history',[])+[{k:item[k] for k in ('status','attempts','fetched_at')}]
        atomic_json(path,item);results.append(item)
        atomic_json(root/'progress.json',{'results':results,'requests_this_run':used,'total_tasks':len(tasks)})
    result={'out_dir':str(root),'requests_this_run':used,'results':results,
        'pending':sum(r['status']=='pending' or (r['status']=='failed' and r['attempts']<2) for r in results),
        'exact_strategy_validation':False,'metrics':None}
    atomic_json(root/'summary.json',result);return result


def backfill(state_dir,manifest,max_requests=100,provider=fetch):
    import fcntl
    from .store import digest
    root=Path(state_dir)/'backfill'/digest(manifest)[:20];root.mkdir(parents=True,exist_ok=True)
    with (root/'lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        return _backfill(state_dir,manifest,max_requests,provider)
