#!/usr/bin/env python3
"""At most eight sequential daily_basic HTTP requests, no retries or HTSC.
Historical completed-date probe only; never calls pipeline or publishes data.
"""
import hashlib,json,os,sys,time,urllib.request
from datetime import datetime
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from concepts_data import CONCEPTS
out=Path(sys.argv[1]).resolve();out.mkdir(parents=True,exist_ok=True)
if (out/'probe.json').exists(): raise SystemExit('probe receipt exists; refuse repeated paid requests')
day='20260924';fields='ts_code,trade_date,pe_ttm,pb,total_mv,turnover_rate'
calendar=json.loads((ROOT/'.cron_state/mt1/runs/2026-09-24-evening/calendar.json').read_text())
assert any(r['date']=='2026-09-24' and r['is_open'] for r in calendar['days'])
assert datetime.now().astimezone()>datetime.fromisoformat('2026-09-24T15:00:00+08:00')
trace=[];start=time.monotonic();summary={'trade_date':day,'request_budget':8,'deadline_seconds':160,'huatai_requests':0}
def save():
 summary.update(requests=trace,wall_seconds=time.monotonic()-start)
 (out/'probe.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
def call(params,fs=fields):
 if len(trace)>=8 or time.monotonic()-start>135:raise RuntimeError('probe_budget')
 time.sleep(.6);s=time.monotonic();rec={'params':params,'fields':fs,'started_at':datetime.now().astimezone().isoformat()};trace.append(rec)
 req=urllib.request.Request('https://api.tushare.pro',data=json.dumps({'api_name':'daily_basic','token':os.environ['TUSHARE_TOKEN'],'params':params,'fields':fs}).encode(),headers={'Content-Type':'application/json'})
 try:
  with urllib.request.urlopen(req,timeout=20) as r: body=json.load(r)
  raw=json.dumps(body,ensure_ascii=False).encode();p=out/f'response-{len(trace)}.json';p.write_bytes(raw)
  rec.update(code=body.get('code'),path=str(p),sha256=hashlib.sha256(raw).hexdigest())
  if body.get('code')!=0:raise RuntimeError('provider_error:'+str(body.get('code'))+':'+str(body.get('msg')))
  rows=[dict(zip(body['data']['fields'],v)) for v in body['data']['items']];rec['rows']=len(rows);return rows
 finally:rec['wall_seconds']=time.monotonic()-s;save()
try:
 rows=[]
 for page in range(3):
  batch=call({'trade_date':day,'limit':'6000','offset':str(page*6000)})
  rows+=batch
  if len(batch)<6000:break
 else:raise RuntimeError('pagination_not_exhausted_within_budget')
 keys=[(r['ts_code'],r['trade_date']) for r in rows]
 assert len(keys)==len(set(keys)) and all(r['trade_date']==day for r in rows),'duplicate_or_wrong_vintage'
 small0=call({'trade_date':day,'limit':'2','offset':'0'})
 small2=call({'trade_date':day,'limit':'2','offset':'2'})
 summary['pagination_verified']=len(small0)==len(small2)==2 and small0==rows[:2] and small2==rows[2:4]
 assert summary['pagination_verified'],'provider_does_not_honor_limit_offset_order'
 by={r['ts_code']:r for r in rows}
 recap=json.loads(Path('/tmp/evening_recap_2026-09-24.json').read_text())
 needed=sorted({c for s in recap['picks'] for c,_ in CONCEPTS.get(s,[]) if c.endswith(('.SZ','.SH','.BJ'))})
 summary.update(batch_rows=len(rows),selected_unique=len(needed),missing=sorted(set(needed)-set(by)))
 comparisons=[]
 for code in ('603986.SH','688525.SH'):
  single=call({'ts_code':code,'trade_date':day},'pe_ttm,pb,total_mv,turnover_rate')
  projected={k:by[code][k] for k in ['pe_ttm','pb','total_mv','turnover_rate']}
  comparisons.append({'code':code,'equal':single==[projected],'single':single,'projected':projected})
 summary['comparisons']=comparisons
 assert all(r['equal'] for r in comparisons),'batch_projection_not_equal'
 summary['status']='verified_candidate'
except Exception as e:summary.update(status='limited',limitation=str(e))
finally:save()
print(json.dumps(summary,ensure_ascii=False,indent=2))
