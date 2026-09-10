#!/usr/bin/env python3
"""Leakage-controlled backtest for narrative alpha filters.

Uses only event metadata plus OHLCV available strictly before the event baseline.
Outcomes come from narrative_perf.jsonl at an exact natural-day horizon.
No future labels are used to create features or rank same-day candidates.
"""
from __future__ import annotations
import argparse, csv, datetime as dt, json, math, statistics, subprocess, sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

HERE=Path(__file__).resolve().parent
EVENTS=HERE/'narrative_events.jsonl'; PERF=HERE/'narrative_perf.jsonl'; TUSHARE=HERE/'tushare.py'; LAYERS=HERE/'ticker_layer.yaml'
DAILY_PARQUET=Path.home()/'.homespace/data/market-tools/daily/daily.parquet'
CACHE=HERE/'.cron_state'/'narrative_backtest_features.jsonl'


def read_jsonl(p): return [json.loads(x) for x in open(p,encoding='utf-8') if x.strip()]
def median(xs): return statistics.median(xs) if xs else None

def source_tier(e):
 s=(e.get('source') or '').lower(); u=(e.get('url') or '').lower()
 if any(x in s for x in ['公司公告','交易所','国家发展改革委','国家发改委','工信部','国务院','海关总署','公司半年报']): return 'primary'
 if any(x in s+u for x in ['trendforce','counterpoint','idc','semi','reuters','财联社','证券时报','第一财经','每日经济新闻','科创板日报']): return 'industry'
 if any(x in s+u for x in ['百家号','九方智投','新浪财经','快科技','it之家']): return 'aggregator'
 return 'major_media'

def event_type(e):
 # Stable title-only P1 buckets; avoids dependence on mutable current YAML.
 t=e.get('title','')
 rules=[
 ('confirmed_order',['中标','中标通知','正式合同','采购订单','供货合同','订单已锁定','签订采购合同']),
 ('company_capex_committed',['正式开工','设备采购','设备进场','竣工投产','已投产','产线投产']),
 ('financing_capex',['定增','募集资金','拟募资','配股','可转债募资']),
 ('industry_supply_expansion',['新增产能','产能翻倍','年产能','扩建产能','产能规划']),
 ('capacity_plan',['拟投资','计划投资','规划建设','签约落地','战略合作','意向协议','框架采购','计划开工','项目规划']),
 ('quant_increment',['涨价','价格上涨','提价','毛利率提升','单价提升','asp提升','供需缺口','短缺']),
 ('policy',['国标','补贴','以旧换新','国务院','工信部','发改委','财政部','央行','试点']),
 ('recap',['研报','目标价','买入评级','首予','分析师']),
 ('trailing',['半年报','年报披露','业绩预告','历史新高','销量同比','出货量同比','市占率']),]
 lo=t.lower()
 for k,kws in rules:
  if any(x.lower() in lo for x in kws): return k
 return 'other'

def is_lagging(e):
 f=e.get('features') or {}
 if f.get('_human_labeled'): return bool(f.get('is_lagging_indicator'))
 return event_type(e) in {'trailing','recap'}

def specificity(e):
 f=e.get('features') or {}
 if f.get('_human_labeled') and f.get('specificity'): return f['specificity']
 n=len(e.get('tickers') or []); return 'narrow' if n<=2 else 'medium' if n<=4 else 'broad'

def rows_csv(api,retries=3,**kw):
 """Fetch CSV, preferring the local point-in-time Parquet store.

 This keeps the historical backtest reproducible and independent of live API
 credentials.  A live Tushare query is only the fallback for an actual cache
 miss; transient empty output is never accepted as valid history.
 """
 fields=['ts_code','trade_date','open','high','low','close','vol','amount','pct_chg']
 if api=='daily' and DAILY_PARQUET.exists():
  try:
   import duckdb
   clauses=[];args=[]
   if kw.get('ts_code'):clauses.append('ts_code=?');args.append(kw['ts_code'])
   if kw.get('trade_date'):clauses.append('trade_date=?');args.append(kw['trade_date'])
   if kw.get('start_date'):clauses.append('trade_date>=?');args.append(kw['start_date'])
   if kw.get('end_date'):clauses.append('trade_date<=?');args.append(kw['end_date'])
   sql='SELECT '+','.join(fields)+' FROM read_parquet(?)'
   params=[str(DAILY_PARQUET)]
   if clauses:sql+=' WHERE '+' AND '.join(clauses);params+=args
   sql+=' ORDER BY trade_date'
   rows=duckdb.connect().execute(sql,params).fetchall()
   if rows:return [dict(zip(fields,row)) for row in rows]
  except Exception as exc:
   print(f'local parquet query failed, using Tushare: {exc}',file=sys.stderr)
 cmd=['python3',str(TUSHARE),api]+[f'{k}={v}' for k,v in kw.items()]+['--fields='+','.join(fields),'--csv']
 last=''
 for attempt in range(1,retries+1):
  try: proc=subprocess.run(cmd,capture_output=True,text=True,timeout=90)
  except subprocess.TimeoutExpired: last='timeout'
  else:
   if proc.returncode==0:
    rows=list(csv.DictReader(proc.stdout.splitlines()))
    if rows:return rows
    last='empty response'
   else:last=(proc.stderr or proc.stdout or f'exit {proc.returncode}').strip()[-300:]
  if attempt<retries:subprocess.run(['sleep',str(attempt)],check=False)
 raise RuntimeError(f"data fetch {api} failed after {retries} attempts ({kw}): {last}")

_BATCH_DAILY: dict[str,list[dict]] = {}
def load_daily_range(start_date: str, end_date: str) -> list[dict]:
 """Fetch all A-share bars one trade date at a time; ~180 calls instead of pair-level 442."""
 key=f'{start_date}:{end_date}'
 if key in _BATCH_DAILY:return _BATCH_DAILY[key]
 # trade calendar gives exact open dates and avoids empty-day calls.
 cmd=['python3',str(TUSHARE),'trade_cal',f'start_date={start_date}',f'end_date={end_date}','is_open=1','--fields=cal_date','--csv']
 p=subprocess.run(cmd,capture_output=True,text=True,timeout=60); dates=[]
 if p.returncode==0:
  dates=[r['cal_date'] for r in csv.DictReader(p.stdout.splitlines())]
 rows=[]
 for i,d in enumerate(dates,1):
  rs=rows_csv('daily',trade_date=d);rows.extend(rs)
  if i%25==0:print(f'daily snapshots {i}/{len(dates)}',flush=True)
 _BATCH_DAILY[key]=rows;return rows

def pre_features(code,base_date,bars_by_code=None):
 end=(dt.datetime.strptime(base_date,'%Y%m%d').date()-dt.timedelta(days=1)).strftime('%Y%m%d')
 start=(dt.datetime.strptime(end,'%Y%m%d').date()-dt.timedelta(days=240)).strftime('%Y%m%d')
 bars=(bars_by_code or {}).get(code)
 if bars is None: bars=rows_csv('daily',ts_code=code,start_date=start,end_date=end)
 bars=[x for x in bars if start<=x.get('trade_date','')<=end]
 try:
  bars=sorted(bars,key=lambda x:x['trade_date']); closes=[float(x['close']) for x in bars]; vols=[float(x['vol']) for x in bars]
 except: return {}
 if len(closes)<21:return {}
 def ret(n): return (closes[-1]/closes[-1-n]-1)*100 if len(closes)>n else None
 lo=min(closes[-120:]); hi=max(closes[-120:]); pos=(closes[-1]-lo)/(hi-lo)*100 if hi>lo else 50
 v5=statistics.mean(vols[-5:]); v20=statistics.mean(vols[-20:]); vr=v5/v20 if v20 else None
 return {'pre_ret_5':ret(5),'pre_ret_20':ret(20),'pre_ret_60':ret(60),'position_120d':pos,'volume_ratio_5_20':vr,'pre_bar_date':bars[-1]['trade_date']}

def exact_outcomes(horizon):
 out={}
 for p in read_jsonl(PERF):
  if p.get('days_since_event')==horizon: out[(p['event_ts'],p['code'])]=p
 return out

def build(horizon,refresh=False):
 import yaml
 events={e['ts']:e for e in read_jsonl(EVENTS)}; outcomes=exact_outcomes(horizon); layers=yaml.safe_load(open(LAYERS)) or {}
 cached={}
 if CACHE.exists() and not refresh:
  for x in read_jsonl(CACHE):
   if x.get('pre_bar_date'):cached[(x['code'],x['base_date'])]=x
 needed=sorted({(code,p['baseline_date']) for (ts,code),p in outcomes.items() if code.endswith(('.SH','.SZ','.BJ'))})
 missing=[x for x in needed if x not in cached]; new=[]
 bars_by_code=defaultdict(list)
 if missing:
  # One range request per unique ticker is much cheaper than pair-level calls
  # and avoids 600+ all-market snapshot requests for sparse old anchors.
  by_code_dates=defaultdict(list)
  for code,bd in missing:by_code_dates[code].append(bd)
  def fetch_one(item):
   code,bds=item
   start=(dt.datetime.strptime(min(bds),'%Y%m%d').date()-dt.timedelta(days=240)).strftime('%Y%m%d')
   end=(dt.datetime.strptime(max(bds),'%Y%m%d').date()-dt.timedelta(days=1)).strftime('%Y%m%d')
   return code,rows_csv('daily',ts_code=code,start_date=start,end_date=end)
  with ThreadPoolExecutor(max_workers=8) as ex:
   futs=[ex.submit(fetch_one,item) for item in sorted(by_code_dates.items())]
   for i,f in enumerate(as_completed(futs),1):
    code,bars=f.result();bars_by_code[code]=bars
    if i%20==0:print(f'ticker histories {i}/{len(futs)}',flush=True)
 for i,(code,bd) in enumerate(missing,1):
  feat=pre_features(code,bd,bars_by_code)
  if not feat.get('pre_bar_date'):raise RuntimeError(f'no usable pre-event bars for {code} baseline={bd}')
  x={'code':code,'base_date':bd,**feat};cached[(code,bd)]=x;new.append(x)
 if new:
  CACHE.parent.mkdir(exist_ok=True)
  tmp=CACHE.with_suffix(CACHE.suffix+'.tmp')
  tmp.write_text('\n'.join(json.dumps(x,ensure_ascii=False) for x in cached.values())+'\n')
  tmp.replace(CACHE)
 ds=[]
 for (ts,code),p in outcomes.items():
  e=events.get(ts); pf=cached.get((code,p['baseline_date']),{})
  if not e or not pf.get('pre_bar_date'):continue
  tk=next((t for t in e.get('tickers',[]) if t.get('code')==code),{})
  side=p.get('side','+'); signed_ex=p.get('excess_pct') if side=='+' else -p.get('excess_pct',0); signed_sec=p.get('excess_vs_sector') if side=='+' else -(p.get('excess_vs_sector') or 0)
  ds.append({**pf,'event_ts':ts,'code':code,'name':p.get('name'),'date':e.get('pub_date') or e.get('trade_date'),'subdomain':e.get('subdomain'),'score':e.get('score',0),'late_stage':bool(e.get('late_stage')),
   'event_type_bt':event_type(e),'source_tier_bt':source_tier(e),'lagging_bt':is_lagging(e),'specificity_bt':specificity(e),'ticker_layer_bt':(layers.get(e.get('subdomain'),{}) or {}).get(code),
   'ticker_rank':next((i+1 for i,t in enumerate(e.get('tickers',[])) if t.get('code')==code),99),'signed_excess':signed_ex,'signed_sector':signed_sec,'hit':signed_ex>0,'strict':bool(p.get('hit_strict')),'title':e.get('title')})
 return ds

def metrics(rs):
 if not rs:return {'n':0}
 ex=[r['signed_excess'] for r in rs]; sec=[r['signed_sector'] for r in rs if r.get('signed_sector') is not None]
 return {'n':len(rs),'events':len(set(r['event_ts'] for r in rs)),'hit_rate':sum(r['hit'] for r in rs)/len(rs)*100,'median_excess':median(ex),'mean_excess':statistics.mean(ex),'strict_rate':sum(r['strict'] for r in rs)/len(rs)*100,'median_sector':median(sec),'p10':sorted(ex)[max(0,int(.1*len(ex))-1)]}

def filt(r,kind):
 if kind=='legacy':return True
 if kind=='content':return (not r['late_stage'] and not r['lagging_bt'] and r['specificity_bt']!='broad' and r['event_type_bt'] not in {'capacity_plan','financing_capex','industry_supply_expansion','recap','trailing'})
 if kind=='mapping':return filt(r,'content') and r['ticker_layer_bt'] in {'core_pure','core_partial'} and r['ticker_rank']<=2
 if kind=='p1':return filt(r,'mapping') and r.get('position_120d',101)<=80 and (r.get('pre_ret_20') is None or r['pre_ret_20']<=25) and (r.get('volume_ratio_5_20') is None or r['volume_ratio_5_20']<=1.8)
 if kind=='p1_strict':return filt(r,'mapping') and r.get('position_120d',101)<=70 and (r.get('pre_ret_20') is None or r['pre_ret_20']<=15) and (r.get('volume_ratio_5_20') is None or r['volume_ratio_5_20']<=1.5)

def print_table(ds):
 print('| strategy | n | events | hit% | median excess | strict% | median vs sector | mean excess | p10 |')
 print('|---|---:|---:|---:|---:|---:|---:|---:|---:|')
 for k in ['legacy','content','mapping','p1','p1_strict']:
  m=metrics([r for r in ds if filt(r,k)])
  print(f"| {k} | {m.get('n',0)} | {m.get('events',0)} | {m.get('hit_rate',0):.1f} | {m.get('median_excess',0):+.2f}% | {m.get('strict_rate',0):.1f} | {m.get('median_sector',0):+.2f}% | {m.get('mean_excess',0):+.2f}% | {m.get('p10',0):+.2f}% |")

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--horizon',type=int,default=14);ap.add_argument('--refresh',action='store_true');ap.add_argument('--json-out');a=ap.parse_args()
 ds=build(a.horizon,a.refresh)
 if not ds:raise RuntimeError('backtest dataset is empty; refusing to report misleading zero metrics')
 print('dataset',len(ds),'range',min(r['date'] for r in ds),max(r['date'] for r in ds));print_table(ds)
 if a.json_out:Path(a.json_out).write_text('\n'.join(json.dumps(x,ensure_ascii=False) for x in ds)+'\n')
if __name__=='__main__':main()
