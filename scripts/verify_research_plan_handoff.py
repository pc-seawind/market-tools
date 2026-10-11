"""Isolated synthetic CLI handoff; never uses production scope/state/network."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone, timedelta

ROOT = Path(__file__).resolve().parents[1]


def verify(out):
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    now = datetime.now(timezone.utc)
    today = now.date()
    stamp = lambda days: (now + timedelta(days=days)).isoformat()
    day = lambda days: (today + timedelta(days=days)).isoformat()
    def save(name, value):
        p = out / name
        p.write_text(json.dumps(value, ensure_ascii=False, indent=2))
        return p
    scope = save('scope.json', {'scope_epoch':'synthetic-signoff', 'reset_at':stamp(-10),
                              'confirmed_holdings':[], 'active_candidates':[]})
    original = scope.read_bytes()
    source = out / 'source.txt'
    source.write_text('SYNTHETIC ONLY: not a real company or investment conclusion')
    sha = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    env = {**os.environ, 'MT1_TRACKING_SCOPE':str(scope)}
    calls = []
    def cli(label, *args):
        cmd = [sys.executable, str(ROOT/'mt1.py'), '--scope',str(scope),
               '--state-dir',str(out/'state'), *map(str,args)]
        cp = subprocess.run(cmd, cwd=ROOT, env=env, text=True, capture_output=True, timeout=30)
        (out/(label+'.stdout')).write_text(cp.stdout)
        (out/(label+'.stderr')).write_text(cp.stderr)
        calls.append({'label':label, 'argv':cmd, 'exit_code':cp.returncode})
        assert cp.returncode == 0, cp.stderr
        value = json.JSONDecoder().raw_decode(cp.stdout)[0]
        if args[0]=='run':
            value = json.loads((out/'state/runs'/str(args[args.index('--run-id')+1])/'report.json').read_text())
        return value
    event = {'scope_epoch':'synthetic-signoff','scope_sha256':sha(scope), 'domain':'investment',
             'reviewer':'SYNTHETIC reviewer','code':'999999.SZ','state':'watch',
             'reason':'SYNTHETIC thesis','request_id':'research-v1','expected_version':0,
             'reviewed_at':stamp(0),'business_logic':'SYNTHETIC business evidence', 'risk':'SYNTHETIC risk',
             'next_review':stamp(2),'research_horizon_end':stamp(60),
             'milestones':[{'at':stamp(7),'description':'SYNTHETIC near-term verification'}],
             'sources':[{'url':'https://example.invalid/synthetic','path':str(source),'sha256':sha(source),
                         'published_at':stamp(-1),'fetched_at':stamp(0)}]}
    first = cli('research-v1','research-event','--input',save('research-v1.json',event))
    fixture = save('fixture.json', {})
    run = cli('weekend-before','run','sunday','--fixture',fixture,'--run-id','synthetic-before',
              '--investment-dir',out/'investment')
    payload = dict(plan_id='synthetic-watch', code=event['code'],market='CN',episode='watch-episode-1',
                   scope_epoch='synthetic-signoff',research_event_id='research-v1',
                   original_reason='SYNTHETIC thesis',original_date=day(0),original_deadline=day(60),
                   reference_price=None,channel='VALUE',state='WATCH',holding_status='not_held',
                   unheld_direction='WATCH',held_direction='WATCH',actual_cost=None,
                   evidence=[{'url':'https://example.invalid/synthetic','date':day(0),'claim':'SYNTHETIC evidence'}],
                   milestones=[{'date':day(7),'condition':'SYNTHETIC verification'}],price_condition=None,
                   risk_boundary={'condition':'SYNTHETIC invalidation','source_url':'https://example.invalid/synthetic'},
                   invalidation='SYNTHETIC thesis fails',review_due=day(2),qualification='final',reviewer='SYNTHETIC reviewer')
    bundle = {'phase':'sunday','reviewer':'SYNTHETIC reviewer','source_run_id':'synthetic-before',
              'report_context':'隔离合成正式签审入口验证；非自然执行，非投资建议。',
              'reviewed_plan_ids':['synthetic-watch'],'review_items':[], 'method_events':[],
              'research':{'fetch_status':'ok','sources':[{'url':'https://example.invalid/synthetic',
                          'content_path':str(source),'content_hash':sha(source),'fetched_at':stamp(0)}]},
              'plan_events':[{'id':'synthetic-watch','expected_version':0,'request_id':'plan-v1',
                              'reason':'SYNTHETIC WATCH signoff, not BUY','payload':payload}]}
    signed = cli('finalize-v1','finalize','--input',save('finalize-v1.json',bundle),'--investment-dir',out/'investment')
    assert signed['errors']==[] and signed['changes'][0]['after']['version']==1
    assert signed['watchlist']['pending']==[]
    retry = cli('finalize-v1-retry','finalize','--input',out/'finalize-v1.json','--investment-dir',out/'investment')
    assert retry['changes'][0]['after']['version']==1
    event.update(request_id='research-v2',expected_version=1,reason='SYNTHETIC renewed evidence')
    cli('research-v2','research-event','--input',save('research-v2.json',event))
    stale = cli('plans-stale','plans')
    assert not stale
    rejected = cli('finalize-stale','finalize','--input',out/'finalize-v1.json','--investment-dir',out/'investment')
    assert any('plan_outside_tracking_scope' in x['error'] for x in rejected['errors'])
    bundle['plan_events']=[{'id':'synthetic-watch','expected_version':1,'request_id':'plan-v2',
                           'reason':'SYNTHETIC renewed signoff','payload':{'research_event_id':'research-v2'}}]
    renewed = cli('finalize-v2','finalize','--input',save('finalize-v2.json',bundle),'--investment-dir',out/'investment')
    assert renewed['errors']==[] and renewed['changes'][0]['after']['version']==2
    plans = cli('plans-current','plans')
    assert len(plans)==1 and plans[0]['research_event_id']=='research-v2'
    # Same actual daily consumer, deterministic synthetic quotes injected only at provider seam.
    from mt1.daily_tracking import track
    daily=track(str(scope),now=now,fetch=lambda code:[{'date':day(0),'close':10}],
                gates={'CN':{'allowed':False,'expected_date':day(0),'reason':'synthetic offline'}})
    save('daily.json',daily)
    assert daily['rows'][0]['research_event_id']=='research-v2'
    assert daily['rows'][0]['holding_status']=='not_held'
    for phase in ('saturday','sunday'):
        result=cli('weekend-'+phase,'run',phase,'--fixture',fixture,'--run-id','synthetic-'+phase,
                   '--investment-dir',out/'investment')
        assert result['plans'][0]['version']==2
        body=Path(result['report_path']).read_text()
        assert '999999.SZ' in body and 'WATCH' in body and '研究' in body
    import sqlite3
    with sqlite3.connect(out/'state/plans.db') as db:
        assert db.execute("select count(*) from events where entity='plan:synthetic-watch'").fetchone()[0]==2
    assert scope.read_bytes()==original
    save('receipt.json',{'mode':'SYNTHETIC isolated CLI; NOT natural dispatch or company research',
                         'calls':calls,'watch_only':True,'scope_unchanged':True,
                         'assertions':['CLI research-event -> finalize -> Store.apply',
                         'retry idempotent','old plan invalid after research version change',
                         'new version final WATCH signed without BUY or method promotion',
                         'daily current research event','saturday/sunday current formal plan'],
                         'files':{str(p.relative_to(out)):sha(p) for p in out.rglob('*') if p.is_file()}})
    return out


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',required=True)
    print(verify(p.parse_args().out))
