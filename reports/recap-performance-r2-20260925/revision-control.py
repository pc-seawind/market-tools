import os,json,time,urllib.request
from pathlib import Path
from datetime import datetime
out=Path(__file__).resolve().parent
assert not (out/'revision-control.json').exists(),'do not repeat already-paid probe'
s=time.monotonic();params={'ts_code':'300223.SZ','trade_date':'20260924'}
req=urllib.request.Request('https://api.tushare.pro',data=json.dumps({'api_name':'daily_basic','token':os.environ['TUSHARE_TOKEN'],'params':params,'fields':'pe_ttm,pb,total_mv,turnover_rate'}).encode(),headers={'Content-Type':'application/json'})
with urllib.request.urlopen(req,timeout=20) as response: body=json.load(response)
(out/'revision-control.json').write_text(json.dumps({'started_at':datetime.now().astimezone().isoformat(),'wall_seconds':time.monotonic()-s,'params':params,'body':body,'total_probe_requests_including_initial':6,'huatai_requests':0},indent=2))
