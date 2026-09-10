#!/usr/bin/env python3
"""Offline follow-up: immutable explanation overlay + real restricted asof draft."""
import argparse,gzip,json,sqlite3,subprocess,sys
from collections import Counter
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from mt1.cache_union import Union
from mt1.evidence import file_ref
from mt1.raw_asof import raw_sources,build_draft
from mt1.store import digest
from mt1.suspension_evidence import explain
from mt1.conflict_attribution import ratio_diagnostic


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',required=True);a=p.parse_args()
    out=Path(a.output);out.mkdir(parents=True,exist_ok=False)
    base=ROOT/'.cron_state/mt1/work-0754-r2';uroot=ROOT/'.cron_state/mt1/work-0754-union-after'
    def save(n,v):(out/n).write_text(json.dumps(v,ensure_ascii=False,indent=2))
    def git(*args):return subprocess.check_output(['git',*args],cwd=ROOT,text=True,timeout=20).strip()
    rows=[json.loads(line) for line in gzip.open(uroot/'warmup-holes.jsonl.gz','rt')]
    rows=[r for r in rows if r['classification']=='unexplained_missing_row' and r['missing'].get('daily')=='missing_row']
    es=json.loads((ROOT/'docs/MT-1.0-work-0754-r2-suspension-evidence.json').read_text())
    overlay=explain(rows,es);save('explanations.json',overlay)
    sources=[]
    for name in ('MT-1.0-work-0754-backfill.json','MT-1.0-work-0754-delisted-actions.json'):
        m=json.loads((ROOT/'docs'/name).read_text());sources+=raw_sources(ROOT/'.cron_state/mt1/backfill'/digest(m)[:20]/'summary.json')
    sources+=raw_sources(ROOT/'.cron_state/mt1/backfill/e043b8ebe657ea08de43/summary.json')
    precision=json.loads((base/'precision/summary.json').read_text());v=precision[6]
    if file_ref(v['path'])['sha256']!=v['sha256']:raise ValueError('valuation hash changed')
    data=json.loads(Path(v['path']).read_text())['data'];valuation=[dict(zip(data['fields'],r)) for r in data['items']]
    sources.append({'api':'daily_basic','params':{'ts_code':'600519.SH','trade_date':'20240408'},'source':file_ref(v['path']),'fetched_at':v['fetched_at'],'rows':valuation})
    u=object.__new__(Union);u.db=sqlite3.connect('file:'+str(uroot/'union.sqlite')+'?mode=ro',uri=True)
    sessions=sorted(json.loads((uroot/'universe.json').read_text())['by_day'])
    draft=build_draft('600519.SH','贵州茅台','2024-04-08T16:00:00+08:00',sessions,u,sources);u.db.close()
    text=base/'public-fallback/moutai-original.txt';lines=text.read_text().splitlines()
    needles=['147,693,604,994.14','74,734,071,550.75','追溯调整']
    excerpts=[{'line':i+1,'text':line} for i,line in enumerate(lines) if any(n in line for n in needles)]
    if not all(any(n in line for line in lines) for n in needles):raise ValueError('original filing markers absent')
    history={}
    for path in ('concepts_data.py','concept_etf_map.yaml'):
        history[path]={'current':file_ref(ROOT/path),'all_local_refs_history':git('log','--all','--reverse','--format=%H %aI %s','--',path),
            'commits_before_decision':git('rev-list','--all','--before=2024-04-08T16:00:00+08:00','--',path)}
    draft['original_filing_trace']={'source_manifest':file_ref(base/'public-fallback/summary.json'),
        'pdf':file_ref(base/'public-fallback/moutai-original.pdf'),'text':file_ref(text),'issuer_index':file_ref(base/'public-fallback/moutai-index.html'),
        'publication_date_on_index':'2024-04-03','retrieval_time_is_not_publication_time':True,'excerpts':excerpts,
        'candidate_2023_revenue_yuan':'147693604994.14','candidate_2023_parent_profit_yuan':'74734071550.75',
        'warning':'PDF explicitly restates comparison years. Do not propagate 2023-restated 2022 figures into 2022 decisions.',
        'version_certification':'unknown: no contemporaneous capture or complete revision chain; PDF candidates not admitted'}
    draft['original_concept_trace']={'repository_shallow':git('rev-parse','--is-shallow-repository'),'history':history,
        'scope':'all locally available refs only, not proof that no external archive exists',
        'current_concept':'白酒 (消费龙头)','original_2024_membership':None,'original_2024_context':None,
        'recovery':'supply contemporaneous original concept/member/weight/context archives; official Shenwan is not a substitute',
        'stop_reason':'available repository history starts in 2026; further quote downloads cannot recover 2024 custom pool/context'}
    save('600519-restricted-draft.json',draft)
    cs=json.loads((base/'conflict-attribution/conflicts.json').read_text());sm=json.loads((base/'conflict-attribution/sources.json').read_text());groups={}
    for c in cs:
        if c['api']!='adj_factor':continue
        old=[s['row']['adj_factor'] for s in c['sources'] if sm[str(s['source_id'])]['path'].endswith('.parquet')]
        new=[s['row']['adj_factor'] for s in c['sources'] if '/backfill/' in sm[str(s['source_id'])]['path']]
        if old and new:groups.setdefault(c['code'],[]).append((old[0],new[0]))
    save('ratio-variation.json',{k:ratio_diagnostic(v) for k,v in groups.items()})
    save('daily-differences.json',{'fields':dict(Counter(f for c in cs if c['api']=='daily' for f in c['fields'])),
        'classifications':dict(Counter(d['kind'] for c in cs if c['api']=='daily' for d in c['differences'].values())),
        'reviewer_key':[c for c in cs if c['api']=='daily' and c['code']=='000100.SZ' and c['day']=='2026-06-02']})
    save('summary.json',{'status':'incomplete','explanation_counts':dict(Counter(r['explanation'] for r in overlay)),
        'physical_holes_unchanged':True,'PIT_admitted':False,'replay_ready':False,'metrics':None,
        'sample_recompute':draft['recompute'],'artifacts':[file_ref(q) for q in sorted(out.iterdir())]})
    print((out/'summary.json').read_text())
if __name__=='__main__':main()
