"""Frozen cold-query comparison; no external requests, bounded by systemd."""
import csv,dataclasses,hashlib,importlib.util,json,os,subprocess,sys,tempfile,time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import recap_rpc as after
import recap_daily_basic as batch
import sector_picks as picks
import tushare
out=Path(sys.argv[1]).resolve();raw=json.loads((out/'response-1.json').read_text())
panel=list(csv.DictReader(tushare.csv_text(raw['data']).splitlines()))
# Load all original real frozen cache snapshots from R1; never fetch missing data.
frozen={}
import gzip
for snapshot in json.loads(gzip.decompress((out/'frozen-inputs.json.gz').read_bytes())):
 source=snapshot['meta'];raw_snapshot=snapshot['raw'].encode()
 assert hashlib.sha256(raw_snapshot).hexdigest()==source['sha256']
 frozen[(source['api'],source['key'])]=tushare.csv_text(json.loads(raw_snapshot)['data'])
original=json.loads((ROOT/'reports/recap-reliability-20260925/fixed.result.json').read_text())
codes=[(x['stock']['code'],x['stock']['name']) for x in original['evaluations']]
# Six historical singles match; one revised value is verified by a new single
# control. Keep old snapshot in R1; cold comparison uses same current snapshot.
control=json.loads((out/'revision-control.json').read_text())['body']
assert control['code']==0
frozen[('daily_basic',tushare._cache_key('daily_basic',{'ts_code':'300223.SZ','trade_date':'20260924'},batch.FIELDS))]=tushare.csv_text(control['data'])
# All seven independently collected single-name snapshots match the batch.
by={r['ts_code']:r for r in panel}
for c,_ in codes:
 single=list(csv.DictReader(frozen[('daily_basic',tushare._cache_key('daily_basic',{'ts_code':c,'trade_date':'20260924'},batch.FIELDS))].splitlines()))
 assert single==[{k:by[c][k] for k in batch.FIELDS.split(',')}]
records=[]
with tempfile.TemporaryDirectory(prefix='recap-batch-bench-') as d:
 temp=Path(d);tushare.CACHE_DIR=str(temp/'empty-provider-cache');p=temp/'before.py';p.write_bytes(subprocess.check_output(['git','show','8659cd7:recap_rpc.py'],cwd=ROOT))
 spec=importlib.util.spec_from_file_location('before',p);before=importlib.util.module_from_spec(spec);spec.loader.exec_module(before)
 for scenario in ['seven_stock_complete','109_dated_names']:
  results=[]
  for label,module in [('before',before),('after',after)]:
   calls=[];module._last=0
   module.provider_cache_csv=lambda *a:None
   os.environ.update(RECAP_INPUT_HASH='FROZEN-R2',RECAP_RPC_CACHE=str(temp/(scenario+label)),RECAP_NO_HISTORY='1')
   os.environ.pop('RECAP_DAILY_BASIC',None)
   def provider(args,**kw):
    api=args[2];params={a.split('=',1)[0]:a.split('=',1)[1] for a in args[3:] if '=' in a and not a.startswith('--')}
    fields=next(a.split('=',1)[1] for a in args if a.startswith('--fields='));calls.append({'api':api,'params':params,'fields':fields})
    if 'limit' in params:
     # Replay the measured cold panel latency, not an invented zero-cost batch.
     time.sleep(3.1657277);text=tushare.csv_text(raw['data'])
    elif scenario=='109_dated_names':
     time.sleep(.081);text=tushare.csv_text({'fields':batch.FIELDS.split(','),'items':[[by[params['ts_code']][k] for k in batch.FIELDS.split(',')]]})
    else:
     time.sleep(.01);text=frozen[(api,tushare._cache_key(api,params,fields))]
    return subprocess.CompletedProcess(args,0,text,'')
   module.run=provider
   def ts(api,**params):
    args=['python3',str(ROOT/'tushare.py'),api]+[f'--fields={v}' if k=='fields' else f'{k}={v}' for k,v in params.items()]+['--csv']
    return module.csv_rpc(args,api,params)
   picks._ts=ts;start=time.monotonic()
   if label=='after':
    dest=temp/(scenario+'.panel.json');batch.prepare(dest,'20260924','FROZEN-R2',module.csv_rpc)
    os.environ['RECAP_DAILY_BASIC']=str(dest)
   if scenario=='seven_stock_complete':
    with ThreadPoolExecutor(max_workers=2) as pool: values=list(pool.map(lambda c:dataclasses.asdict(picks.compute_stock(*c)),codes))
   else:
    from concepts_data import CONCEPTS
    selected=json.loads(Path('/tmp/evening_recap_2026-09-24.json').read_text())['picks']
    valid=sorted({c for s in selected for c,_ in CONCEPTS.get(s,[]) if c in by and c.endswith(('.SH','.SZ','.BJ'))})
    assert len(valid)==109
    values=[ts('daily_basic',ts_code=c,trade_date='20260924',fields=batch.FIELDS) for c in valid]
   wall=time.monotonic()-start;results.append(values)
   records.append({'scenario':scenario,'version':label,'wall_seconds':wall,'simulated_provider_requests':len(calls),'real_provider_requests':0,'output_sha256':hashlib.sha256(json.dumps(values,sort_keys=True).encode()).hexdigest(),'covered':len(values)})
  assert results[0]==results[1],scenario
summary={'status':'equal','single_snapshot_checks':7,'network_calls':0,'cold_model':'panel latency measured 3.1657s; dated single 81ms; other APIs synthetic10ms; all exact query caches cold; NOT whole cron timing','results':records}
(out/'batch-benchmark.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary,indent=2))
