#!/usr/bin/env python3
"""Read-only evidence extraction; no timestamp inference from file mtime."""
import collections
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from concepts_data import CONCEPTS
OUT=ROOT/'reports/recap-performance-20260925';OUT.mkdir(exist_ok=True)
sources={}
def ref(p):
 p=Path(p);sources[str(p)]={'sha256':hashlib.sha256(p.read_bytes()).hexdigest()};return p

def read(p): return json.loads(ref(p).read_text())
def dt(s):return datetime.fromisoformat(s)
lines=ref(OUT/'historical-journal.txt').read_text().splitlines()
result=[]
for d in range(21,25):
 day=f'2026-09-{d}';events=[]
 for line in lines:
  if line.startswith(day) and '[evening_recap_data]' in line or (line.startswith(day) and 'reversal quota:' in line):
   events.append((dt(line.split()[0]),line))
 stages={};picks=[];last=None
 for when,line in events:
  if 'refresh HTSC' in line: stages['refresh_start']=when.isoformat()
  elif 'stage 1:' in line:
   stages['score_start']=when.isoformat()
   stages['htsc_refresh_wall_s']=(when-dt(stages['refresh_start'])).total_seconds()
  elif 'reversal quota:' in line:
   stages['picks_start']=when.isoformat(); last=when
   stages['score_plus_freshness_wall_s']=(when-dt(stages['score_start'])).total_seconds()
  elif 'picks ' in line and ('TIMEOUT' in line or 'picks ok' in line):
   picks.append({'at':when.isoformat(),'result':line.split('[evening_recap_data]')[1].strip(),
                 'wall_since_previous_boundary_s':(when-last).total_seconds() if last else None})
   last=when
 stages['score_wall_caveat']='score end not independently logged; boundary includes freshness/setup; no summed RPC times'
 report=read(ROOT/f'.cron_state/mt1/runs/{day}-evening/report.json')
 base=next(Path('/home/emox/work/investment/reference/daily-reports').glob(f'202609{d}T*-evening'))
 huatai=read(base/'huatai-evening/complete.json');consumer=read(base/'consumer/consumer-receipt.json')
 starts=[a['started_at'] for r in huatai['results'] for a in r.get('attempts',[]) if a.get('started_at')]
 publication=None
 if (base/'delivery-receipt.json').exists():
  r=read(base/'delivery-receipt.json');publication=r.get('recorded_at',r.get('at'))
 elif (base/'docs-publication-verified-at').exists():publication=ref(base/'docs-publication-verified-at').read_text().strip()
 row={'day':day,'stages':stages,'per_pick':picks,'cache_temperature':'unknown: historical RPC cache-hit trace absent',
      'recap_errors':report['errors'],'huatai_wall_s':(dt(huatai['completed_at'])-min(map(dt,starts))).total_seconds(),
      'huatai_started_at':min(starts),'huatai_completed_at':huatai['completed_at'],
      'huatai_attempts':sum(len(r.get('attempts',[])) for r in huatai['results']),
      'huatai_success':huatai['success'],'consumer_wall_s':(dt(consumer['finished_at'])-dt(consumer['started_at'])).total_seconds(),
      'consumer_started_at':consumer['started_at'],'consumer_finished_at':consumer['finished_at'],
      'technical_asof':consumer['technical_asof'],'company_TA_wall_s':None,'upload_wall_s':None,
      'publication_verified_at':publication,'publication_note':'readback completion, not exact first-send time; missing=unknown'}
 path=Path(f'/tmp/evening_recap_{day}.json')
 if path.exists():
  recap=read(path);row['recap_meta']=recap['meta']
  sectors=list(recap['picks']);codes=[c for s in sectors for c,n in CONCEPTS.get(s,[])];a=[c for c in codes if c.endswith(('.SH','.SZ','.BJ'))]
  row['stock_overlap']={'basis':'current unchanged CONCEPTS projected on archived selected sectors, not historical HTTP proof',
   'selected_sectors':len(sectors),'stock_occurrences':len(codes),'unique':len(set(codes)),
   'a_share_occurrences':len(a),'a_share_unique':len(set(a)),
   'potential_identical_stock_RPC_repeats':5*(len(a)-len(set(a))),
   'repeats':{c:n for c,n in collections.Counter(codes).items() if n>1}}
 result.append(row)
# Sept 24 detached stdout has no timestamps: explicitly do not invent a breakdown.
p=Path('/home/emox/.homespace/detached/1790245869954_mt1-evening-research-collect')
m=read(p/'meta.json');ref(p/'out');ref(p/'status')
result[-1]['detached']={k:m[k] for k in ['started_at','harvested_at','max_runtime_sec']}
result[-1]['detached']['note']='harvested_at is not process completion; out has no per-stage timestamps'
# Verified prior RPC sample: count logical requests and cache hits, not HTTP.
p=ROOT/'reports/recap-reliability-20260925/morning-recovery-final-2026-09-25.json.rpc.jsonl'
rs=[json.loads(x) for x in ref(p).read_text().splitlines()];rs=[r for r in rs if r.get('event')=='finish']
rpc={'logical_requests':len(rs),'recap_cache_hits':sum(r.get('cache_hit',False) for r in rs),
 'trace_envelope_wall_s':max(r['started_at']+r['elapsed_seconds'] for r in rs)-min(r['started_at'] for r in rs),
 'distinct_request_hashes':len({r['input_hash'] for r in rs}),'HTTP_requests':'unknown; CLI may hit underlying cache',
 'note':'9/25 limited recovery, NOT 9/21-24 historical full chain'}
for p in Path('/home/emox/work/investment/reference/operations/weekend-loop-20260912/engine-contracts').glob('*.md'):ref(p)
(OUT/'historical-analysis.json').write_text(json.dumps({'days':result,'prior_trace':rpc,'sources':sources},ensure_ascii=False,indent=2))
for r in result: print(r['day'],r['stages'],r.get('stock_overlap'),r['publication_verified_at'])
