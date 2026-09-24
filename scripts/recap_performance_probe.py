#!/usr/bin/env python3
"""Bounded offline/real-cache-only comparison. No provider, ledger or publication IO.
Run under systemd-run --user --property=RuntimeMaxSec=180.
"""
import csv
import dataclasses
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import tushare
import recap_rpc as after
import sector_picks as picks

out=Path(sys.argv[1]).resolve();out.mkdir(parents=True,exist_ok=True)
# Explicit completed historical input, never invoke the production calendar/pipeline.
stocks=json.loads((ROOT/'reports/recap-reliability-20260925/fixed.result.json').read_text())['evaluations']
codes=[(s['stock']['code'],s['stock']['name']) for s in stocks]
queries=[('daily','trade_date,close,vol,amount',{}),('adj_factor','trade_date,adj_factor',{}),
 ('daily_basic','pe_ttm,pb,total_mv,turnover_rate',{'trade_date':'20260924'}),
 ('daily_basic','trade_date,pe_ttm',{}),('fina_indicator','ann_date,end_date,roe,grossprofit_margin,netprofit_yoy,or_yoy',{})]
source=[]; frozen={}
for code,_ in codes:
 for api,fields,extra in queries:
  params={'ts_code':code,**extra};key=tushare._cache_key(api,params,fields)
  path=Path(tushare.CACHE_DIR)/api/(key+'.json')
  body=tushare._cache_read(api,params,fields)
  if not body or body.get('code')!=0 or not body.get('data',{}).get('items'):
   raise SystemExit('Missing valid existing cache; probe refuses network: '+str(path))
  raw=path.read_bytes();frozen[(api,key)]=tushare.csv_text(body['data'])
  source.append({'path':str(path),'sha256':hashlib.sha256(raw).hexdigest(),'mtime':path.stat().st_mtime,
                 'api':api,'key':key,'rows':len(body['data']['items'])})
(out/'input-manifest.json').write_text(json.dumps(source,indent=2))
# Avoid confidential environment propagation: all children read frozen cache only.
os.environ['TUSHARE_TOKEN']='offline-cache-only';os.environ['TUSHARE_NO_PARQUET']='1'
results=[]
with tempfile.TemporaryDirectory(prefix='recap-performance-') as temp:
 temp=Path(temp)
 old=temp/'before.py';old.write_bytes(subprocess.check_output(['git','show','2dfa148:recap_rpc.py'],cwd=ROOT))
 spec=importlib.util.spec_from_file_location('before_rpc',old);before=importlib.util.module_from_spec(spec);spec.loader.exec_module(before)
 cli=temp/'cache_only_cli.py'
 cli.write_text("import sys,urllib.request\nsys.path.insert(0,"+repr(str(ROOT))+")\nimport tushare\ndef deny(*a,**k): raise RuntimeError('network forbidden in performance probe')\nurllib.request.urlopen=deny\nsys.exit(tushare.main(sys.argv[1:]))\n")
 for scenario in ['real_existing_mtimes','frozen_same_day_warm','frozen_cold']:
  cache=temp/scenario;cache.mkdir()
  for s in source:
   target=cache/s['api']/(s['key']+'.json');target.parent.mkdir(exist_ok=True)
   shutil.copy2(s['path'],target)
   if scenario=='frozen_same_day_warm': os.utime(target,None)
  tushare.CACHE_DIR=str(cache);os.environ['TUSHARE_CACHE_DIR']=str(cache)
  pair=[]
  for label,module in [('before',before),('after',after)]:
   module._last=0
   session=temp/(scenario+'-'+label);os.environ['RECAP_RPC_CACHE']=str(session)
   os.environ['RECAP_RPC_TRACE']=str(out/(scenario+'-'+label+'.jsonl'))
   calls=[];provider_calls=[]
   original_run=module.run
   def run(args,**kwargs):
    calls.append(args)
    if scenario=='frozen_cold':
     api=args[2];params={a.split('=',1)[0]:a.split('=',1)[1] for a in args[3:] if '=' in a and not a.startswith('--')}
     fields=next(a.split('=',1)[1] for a in args if a.startswith('--fields='))
     provider_calls.append(args);time.sleep(.01)
     return subprocess.CompletedProcess(args,0,frozen[(api,tushare._cache_key(api,params,fields))],'')
    return original_run([sys.executable,str(cli),*args[2:]],**kwargs)
   module.run=run
   direct=after.provider_cache_csv
   if scenario=='frozen_cold': after.provider_cache_csv=lambda *a:None
   def ts(api,**params):
    args=['python3',str(ROOT/'tushare.py'),api]+[f'--fields={v}' if k=='fields' else f'{k}={v}' for k,v in params.items()]+['--csv']
    return module.csv_rpc(args,api,params)
   picks._ts=ts
   start=time.monotonic()
   with ThreadPoolExecutor(max_workers=2) as pool:
    values=list(pool.map(lambda c: picks.compute_stock(*c),codes))
   elapsed=time.monotonic()-start
   serialized=[dataclasses.asdict(v) if v else None for v in values]
   digest=hashlib.sha256(json.dumps(serialized,sort_keys=True).encode()).hexdigest()
   pair.append(serialized)
   results.append({'scenario':scenario,'version':label,'wall_seconds':elapsed,'cli_calls':len(calls),
     'simulated_provider_calls':len(provider_calls),'real_provider_calls':0,'stocks':len(values),
     'usable':sum(v is not None for v in values),'output_sha256':digest})
   after.provider_cache_csv=direct;module.run=original_run
  assert pair[0]==pair[1],scenario+' output changed'
summary={'baseline_commit':'2dfa148','input_trade_date':'20260924','production_gate_bypassed':False,
 'scope':'compute_stock seven A-share stocks, unchanged complete history; no sector score/model/HTSC/ledger/upload',
 'cold_definition':'synthetic 10ms provider, no real cold-network latency claim',
 'results':results,'all_outputs_equal':True}
(out/'benchmark.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
print(json.dumps(summary,ensure_ascii=False,indent=2))
