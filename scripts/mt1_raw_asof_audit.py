#!/usr/bin/env python3
"""Build real raw-input drafts and settlement refusal evidence, offline only."""
import argparse
from collections import Counter
import json
from pathlib import Path
import socket
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from mt1.raw_asof import raw_sources,build_draft,action_evidence
from mt1.cache_union import Union
from mt1.store import digest
from mt1.evidence import file_ref


def main():
    if socket.gethostname()!='emox-OMEN-30L-Desktop-GT13-0xxx':raise SystemExit('unauthorized host')
    p=argparse.ArgumentParser();p.add_argument('--union',required=True);p.add_argument('--output',required=True);a=p.parse_args()
    uroot=Path(a.union);out=Path(a.output);out.mkdir(parents=True,exist_ok=False)
    def save(name,v):(out/name).write_text(json.dumps(v,ensure_ascii=False,indent=2))
    sources=[];manifest=json.loads((ROOT/'docs/MT-1.0-work-0754-backfill.json').read_text())
    for name in ('MT-1.0-work-0754-backfill.json','MT-1.0-work-0754-delisted-actions.json'):
        m=json.loads((ROOT/'docs'/name).read_text());summary=ROOT/'.cron_state/mt1/backfill'/digest(m)[:20]/'summary.json';sources+=raw_sources(summary)
    # Avoid writable access to immutable union artifact (no DDL or WAL changes).
    import sqlite3
    union=object.__new__(Union);union.db=sqlite3.connect('file:'+str((uroot/'union.sqlite').resolve())+'?mode=ro',uri=True)
    universes=json.loads((uroot/'universe.json').read_text());sessions=sorted(universes['by_day'])
    master=json.loads((ROOT/'.cron_state/mt1/sweeps/2026-09-10/stock_basic.json').read_text());names={r['ts_code']:r['name'] for r in master}
    results=[]
    for code in manifest['codes']:
        r=build_draft(code,names[code],'2024-01-02T16:00:00+08:00',sessions,union,sources);save(code+'.json',r);results.append(r)
    delisted=json.loads((ROOT/'docs/MT-1.0-work-0754-delisted-actions.json').read_text())['selected'];actions=[]
    dividend=[r for s in sources if s['api']=='dividend' for r in s['rows']]
    for row in delisted:
        evidence=action_evidence(dividend,row['ts_code'],'2024-01-02','2024-09-30',row['delist_date'])
        last=union.db.execute("SELECT day,payload,conflicts FROM panel WHERE api='daily' AND code=? AND day<? ORDER BY day DESC LIMIT 1",(row['ts_code'],row['delist_date'][:4]+'-'+row['delist_date'][4:6]+'-'+row['delist_date'][6:])).fetchone()
        evidence.update(code=row['ts_code'],delist_date=row['delist_date'],last_observed_raw_daily=last,
                        last_observation_is_not_final_trading_or_settlement_proof=True)
        actions.append(evidence)
    save('delisted-action-refusals.json',actions)
    union.db.close()
    save('summary.json',{'status':'incomplete','real_drafts':len(results),'draft_types':['stock','financial','membership','sector','context'],
        'technical_features_built':sum('technical' in r['draft_inputs']['stock']['lineage'] for r in results),
        'financial_mapping_candidate_rows':sum(len(r['draft_inputs']['financial']['mapping_candidates']) for r in results),
        'eligible_raw_dividend_events':sum(len(r['actions']['events']) for r in results),
        'delisted_codes':len(actions),'delisted_codes_with_observed_daily':sum(bool(r['last_observed_raw_daily']) for r in actions),
        'admitted_bundles':0,'replay_ready':False,'metrics':None,
        'source_refs':[s['source'] for s in sources],'union_ref':file_ref(uroot/'union.sqlite'),
        'artifacts':[file_ref(q) for q in sorted(out.iterdir()) if q.is_file()]})
    print((out/'summary.json').read_text()[:1000])
if __name__=='__main__':main()
