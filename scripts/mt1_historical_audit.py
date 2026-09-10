#!/usr/bin/env python3
"""R3 reproducible read-only cached-data audit. Run in bounded systemd cgroup.

No API calls, publications, plan writes, watchlist or trades. Writes new evidence
under --output; refuses overwrites and refuses any non-home-ubuntu hostname.
"""
import argparse
import gzip
import json
import socket
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from mt1.historical import universe, holes, next_open, select_version, day, price_features, recompute, rules_version
from mt1.evidence import file_ref, inventory


def main():
    if socket.gethostname()!='emox-OMEN-30L-Desktop-GT13-0xxx':
        raise SystemExit('STOP: only home-ubuntu authorized')
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True)
    args=parser.parse_args();out=Path(args.output);out.mkdir(parents=True,exist_ok=False)
    refs=[]
    def read(path):
        path=ROOT/path;refs.append(file_ref(path));return json.loads(path.read_text())
    def save(name,data):
        path=out/name
        with path.open('x') as f:json.dump(data,f,ensure_ascii=False,indent=2,allow_nan=False)
        return file_ref(path)
    state=Path('.cron_state/mt1')
    audit=state/'data-readiness/20260910T180818506822'
    listed=read(state/'sweeps/2026-09-10/stock_basic.json')
    dead=read(audit/'01-stock_basic.json');paused=read(audit/'02-stock_basic.json')
    rows=[{**r,'list_status':s} for data,s in [(listed,'L'),(dead,'D'),(paused,'P')] for r in data]
    cal=read(audit/'11-trade_cal.json')
    sessions=sorted(day(r['cal_date']) for r in cal if str(r['is_open'])=='1' and '20240102'<=r['cal_date']<='20240930')
    u=universe(rows,sessions);save('universe.json',u)
    summary=read(state/'backfill/e043b8ebe657ea08de43/summary.json')
    panels={k:[] for k in ('daily','adj_factor','stk_limit')};filings=[]
    for task in summary['results']:
        if not task.get('artifact'):continue
        data=read(Path(task['artifact']['path']))
        api=task['task']['api']
        if api in panels:panels[api].extend(data)
        if api in ('income','fina_indicator'):
            for r in data:
                filings.append({'api':api,'row':r,'source':task['artifact'],
                    'fetched_at':task['fetched_at'],'pit_verified':False,
                    'blocker':'original release/revision availability and version identity unverified'})
    save('filing-index.json',filings)
    # Real cached rows deliberately retain unknown PIT. No fabricated attestation.
    pit_checks=[]
    for filing in filings:
        row=filing['row']; announcement=row.get('f_ann_date') or row.get('ann_date')
        if not announcement:
            pit_checks.append({'code':row['ts_code'],'blocker':'announcement date unknown'});continue
        candidate={'period':row['end_date'],'available_at':day(announcement)+'T23:59:59+08:00',
                   'observed_at':day(announcement)+'T23:59:59+08:00','pit_verified':False}
        try:
            select_version([candidate],'2024-09-30T23:59:59+08:00')
            raise AssertionError('unverified real financial row unexpectedly admitted')
        except (ValueError,KeyError) as e:pit_checks.append({'code':row['ts_code'],'blocker':str(e)})
    save('real-pit-rejections.json',pit_checks)
    suspend={'2024-09-30':read(audit/'06-suspend_d.json')}
    h=holes(u['by_day'],panels,suspend)
    hp=out/'holes.json.gz'
    with gzip.open(hp,'wt',encoding='utf8') as f:json.dump(h,f,ensure_ascii=False)
    symbols=sorted({r['ts_code'] for r in panels['daily']})
    small={d:[c for c in codes if c in symbols] for d,codes in u['by_day'].items()}
    slice_holes=holes(small,panels,suspend);save('three-symbol-holes.json',slice_holes)
    fills={}
    for code in symbols:
        fills[code]=next_open(code,'2024-09-27T16:00:00+08:00','buy',sessions,
            panels['daily'],panels['stk_limit'],suspend,panels['adj_factor'],.001,.001)
    save('execution-slice.json',fills)
    feature_checks={}
    for code in symbols:
        try:
            feature_checks[code]=price_features(code,'2024-09-30T16:00:00+08:00',sessions,
                [r for r in panels['daily'] if r['ts_code']==code],
                [r for r in panels['adj_factor'] if r['ts_code']==code])
        except (ValueError,KeyError) as e:feature_checks[code]={'status':'blocked','reason':str(e)}
    save('real-feature-rejections.json',feature_checks)
    save('real-recompute-blocked.json',recompute({'decision_at':'2024-09-30T16:00:00+08:00',
        'rule_version':rules_version(),'inputs':{}}))
    natural=inventory(ROOT/state)
    save('natural-chain-inventory.json',natural)
    report={'recorded_at':datetime.now(timezone.utc).isoformat(),'worker':'home-ubuntu',
        'source_refs':refs,'implementation_refs':[file_ref(ROOT/'mt1/historical.py'),file_ref(Path(__file__))],'input_counts':{'L':len(listed),'D':len(dead),'P':len(paused)},
        'sessions':len(sessions),'universe_rejected':u['rejected'],
        'first_session':sessions[0],'last_session':sessions[-1],
        'first_universe':len(u['by_day'][sessions[0]]),'last_universe':len(u['by_day'][sessions[-1]]),
        'retrospective_master_not_certified_PIT':True,
        'delisted_codes_in_window':len({r['ts_code'] for r in dead if any(r['ts_code'] in codes for codes in u['by_day'].values())}),
        'expected_symbol_sessions':h['expected_symbol_sessions'],'holes':h['hole_count'],
        'hole_classification':dict(Counter(r['classification'] for r in h['holes'])),
        'holes_artifact':file_ref(hp),'three_symbol_holes':slice_holes['hole_count'],
        'filing_rows':len(filings),'pit_verified_filings':0,'execution':fills,
        'natural_schedule_status':natural['phases'],'metrics':None,'promotion':'shadow',
        'incomplete':['D1 release/revision chain','D2 historical master completeness and delisting settlement',
          'D3 historical concept membership and sector features','D4 full panel and suspension coverage',
          'D5 verified asof input feature bundles (adapter implemented, real samples blocked)',
          'D6 corporate-action total-return accounting and execution evidence beyond conservative daily model',
          'D7 gated on D1-D6; no returns computed']}
    save('summary.json',report)
    print(json.dumps({k:report[k] for k in ('sessions','first_universe','last_universe','expected_symbol_sessions','holes','three_symbol_holes','filing_rows','execution')},ensure_ascii=False))


if __name__=='__main__':main()
