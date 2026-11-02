"""Synthetic task/admission separation; no production writes or live APIs."""
import copy
import hashlib
import json
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
import test_research_pool_lifecycle as base
from mt1 import research_pool as pool, research_tasks as tasks
from mt1.scope import load


@pytest.fixture
def case():
    c = base.ResearchPool(); c.setUp()
    try:
        yield c
    finally:
        c.doCleanups()


def task(c, action='start', code=None, **kw):
    rows = {t['code']: t for t in tasks.snapshot(c.scope,c.e['scope_epoch'],c.now)['rows']}
    code = code or c.e['code']; t = rows.get(code, {})
    p = {k:c.e[k] for k in ('scope_epoch','scope_sha256','domain','reviewer')}
    p.update(code=code,action=action,request_id=f'{code}-{action}-{t.get("version",0)}',
             expected_version=t.get('version',0),expected_research_version=t.get('research_version',0),
             reviewed_at=c.now.isoformat(),reason='SYNTHETIC explicit investment action')
    if action in {'start','triage','reopen'}:
        p.update(open_gaps=['cash_bridge','valuation'],next_action='核验现金桥原文',due=t.get('due') or (c.now+timedelta(days=2)).isoformat())
    p.update(kw)
    return tasks.apply(c.scope,p,c.now)


def evidence(c, text):
    src = c.root / hashlib.sha256(text.encode()).hexdigest(); src.write_text(text)
    return {'url':'https://example.invalid/SYNTHETIC/'+src.name,'path':str(src),
            'sha256':hashlib.sha256(src.read_bytes()).hexdigest(),
            'published_at':c.now.isoformat(),'fetched_at':c.now.isoformat()}


def get(c):
    return tasks.snapshot(c.scope,c.e['scope_epoch'],c.now)['rows'][0]


def test_no_progress_due_and_forced_disposition(case):
    first=task(case); due=get(case)['due']
    task(case,'attempt',reason='只改文字/刷新时间')
    r=task(case,'attempt',reason='再次复述旧缺口')
    assert get(case)['status']=='review_required' and get(case)['no_progress']==2
    assert get(case)['due']==due
    with pytest.raises(ValueError,match='mandatory disposition'):
        task(case,'attempt')
    with pytest.raises(ValueError,match='continuation_basis'):
        task(case,'review',decision='continue',open_gaps=['cash_bridge'],next_action='核对现金桥',due=due)
    task(case,'review',decision='archive',disposition_basis='证据不足，优先级下降；不判断公司差',next_action='新披露后显式重开')
    assert get(case)['status']=='archived'
    assert pool.read_events(case.scope,case.e['scope_epoch'])==[]
    assert len(tasks.timeline(case.scope,case.e['scope_epoch']))==4
    assert case.scope.read_bytes()==case.before


def test_progress_requires_new_hash_or_resolved_gap_not_due_or_text(case):
    task(case); src=evidence(case,'new cash source')
    task(case,'attempt',sources=[src]); assert get(case)['no_progress']==0
    task(case,'attempt',sources=[src]); assert get(case)['no_progress']==1
    task(case,'attempt',sources=[src],resolved_gaps=['cash_bridge'],resolution_basis='现金桥缺口已在原文核实')
    assert get(case)['no_progress']==0 and get(case)['open_gaps']==['valuation']
    with pytest.raises(ValueError,match='known gap'):
        task(case,'attempt',resolved_gaps=['unknown'])
    with pytest.raises(ValueError,match='cannot change due'):
        task(case,'attempt',due=(case.now+timedelta(days=5)).isoformat())
    assert get(case)['attempts']==3


def test_due_extension_only_explicit_review_preserves_original(case):
    task(case); original=get(case)['due']; later=(case.now+timedelta(days=4)).isoformat()
    with pytest.raises(ValueError,match='cannot roll forward'):
        task(case,'review',decision='continue',open_gaps=['cash_bridge'],next_action='向披露方核实',due=later,continuation_basis='等待披露方回复')
    task(case,'review',decision='continue',open_gaps=['cash_bridge'],next_action='向披露方核实',due=later,
         continuation_basis='已明确缺口及核实渠道',extension_reason='披露方确认两日内回复')
    t=get(case)
    assert t['due']==later and t['original_due']==original and t['extensions'][0]['from']==original
    assert tasks.timeline(case.scope,case.e['scope_epoch'])[0][1]['task']['due']==original


@pytest.mark.parametrize('action,status',[('wait','waiting'),('block','blocked')])
def test_wait_block_not_active_until_latest_check_and_real_trigger(case,action,status):
    task(case); check=case.now+timedelta(days=3)
    task(case,action,disposition_basis='已穷尽当前来源，等待指定披露',next_action='核对新财报',
         trigger_key='q3-filing',trigger_condition='公司发布新版Q3财报',check_at=check.isoformat())
    scope=load(case.scope); assert pool.review_queue(scope)==[]
    assert get(case)['status']==status
    # Discovery cannot relaunch parked work.
    r=[{'code':case.e['code'],'channel_risk':{}}]
    assert pool.research_queues(r,r,scope)[1]==[]
    with pytest.raises(ValueError,match='new verifiable'):
        task(case,'reopen')
    after=tasks.snapshot(case.scope,case.e['scope_epoch'],check+timedelta(seconds=1))
    assert after['rows'][0]['effective_status']=='review_required'
    assert after['accounting']['opening_active']==0 and after['accounting']['reopened']==1
    case.now=check+timedelta(seconds=1)
    task(case,'review',decision='archive',disposition_basis='最迟检查仍无新材料，搁置',next_action='等披露')
    assert get(case)['status']=='archived'


def test_reopen_verified_new_evidence_cas_generation_and_idempotency(case):
    task(case); task(case,'archive',disposition_basis='暂不优先',next_action='新证据重开')
    src=evidence(case,'a newly published filing')
    r=task(case,'reopen',sources=[src],reason='新财报补齐原缺口')
    assert get(case)['generation']==2
    p={k:v for k,v in r.items() if k not in ('task','version','recorded_at','schema_version')}
    assert tasks.apply(case.scope,p,case.now)==r
    with pytest.raises(ValueError,match='idempotency key reused'):
        tasks.apply(case.scope,{**p,'reason':'different'},case.now)
    task(case,'archive',disposition_basis='仍缺关键材料',next_action='未来触发再核')
    with pytest.raises(ValueError,match='new verifiable'):
        task(case,'reopen',sources=[src])


def test_pending_results_saved_but_archive_does_not_auto_reenter(case):
    case.put(state='pending',research_outcome='partially_researched',gap_or_exit_basis='估值缺口')
    # Minimal triage keeps original due; not a company disposition.
    task(case,'triage'); task(case,'archive',disposition_basis='低优先级搁置',next_action='新版披露后再看')
    case.put(state='pending',research_outcome='partially_researched',gap_or_exit_basis='重复缺口',request_id='saved-result',expected_version=1)
    assert get(case)['status']=='archived'
    with pytest.raises(ValueError,match='explicit evidence-bound reopen'):
        case.put(request_id='admit-without-reopen',expected_version=2)
    assert len(pool.read_events(case.scope,case.e['scope_epoch']))==2


def test_old_task_budget_first_and_new_start_limit_not_storage_limit(case):
    for n in range(26):
        task(case,code=f'old-{n}',due=(case.now+timedelta(seconds=1)).isoformat())
    case.now+=timedelta(seconds=2)
    scope=load(case.scope)
    scope['research_pool']['tasks']=tasks.snapshot(case.scope,case.e['scope_epoch'],case.now)
    rows=[{'code':f'new-{n}','channel_risk':{}} for n in range(30)]
    outside,batch,_=pool.research_queues(rows,rows,scope)
    assert len(outside)==30 and len(batch)==0 and len(pool.review_queue(scope))==20
    assert len(pool.review_queue(scope,limit=3))==3
    with pytest.raises(ValueError,match='overdue backlog'):
        task(case,code='blocked-new-start')
    # A real result may still be stored even though no new starts are budgeted.
    case.put(code='save-only',state='pending',research_outcome='unresearched',gap_or_exit_basis='需研究')
    assert len(tasks.snapshot(case.scope,case.e['scope_epoch'],case.now)['rows'])==27


def test_legacy_pending_unchanged_unknown_repeat_and_first_due(case):
    due=(case.now+timedelta(days=1)).isoformat()
    case.put(state='pending',gap_or_exit_basis='需核实',research_outcome='partially_researched',next_review=due)
    first=pool.read_events(case.scope,case.e['scope_epoch'])
    case.put(state='pending',gap_or_exit_basis='需核实',research_outcome='partially_researched',request_id='p2',expected_version=1,next_review=(case.now+timedelta(days=10)).isoformat())
    case.put(state='pending',gap_or_exit_basis='需核实',research_outcome='partially_researched',request_id='p3',expected_version=2)
    assert get(case)['status']=='review_required' and get(case)['due']==due
    assert pool.read_events(case.scope,case.e['scope_epoch'])[:1]==first
    inv=tasks.inventory(case.scope,case.e['scope_epoch'],case.now)
    assert inv['rows'][0]['unknown_fields']==['open_gaps','next_action']
    assert inv['rows'][0]['research_event_count']==3 and inv['company_dispositions_written']==0


def test_archive_keeps_watch_episode_daily_and_held_review(case):
    case.put(); before=load(case.scope)
    case.put(state='pending',gap_or_exit_basis='补核估值',research_outcome='partially_researched',request_id='pending',expected_version=1)
    task(case,'triage'); task(case,'archive',disposition_basis='暂停补研不是退出',next_action='新证据重开')
    scope=load(case.scope)
    assert len(scope['holdings'])==9 and len(scope['research_pool']['monitor_rows'])==1
    assert scope['research_pool']['monitor_rows'][0]['episode_id']==before['research_pool']['monitor_rows'][0]['episode_id']
    from mt1.daily_tracking import track
    result=track(str(case.scope),fetch=lambda c:[{'date':'20261009','close':10}],gates={'CN':{'expected_date':'2026-10-09'}})
    assert any(r['code']==case.e['code'] and r['research_monitor_episodes'] for r in result['rows'])
    assert get(case)['admission_state']=='pending'
    assert pool.snapshot(case.scope,case.e['scope_epoch'])['episodes'][0]['exit'] is None


def test_completed_only_via_original_admission_or_evidenced_rejection(case):
    task(case)
    with pytest.raises(ValueError,match='invalid task action'):
        task(case,'complete')
    case.put()
    assert get(case)['status']=='completed' and get(case)['completion_event_id']=='test1'
    assert case.e['code'] in load(case.scope)['candidates']
    # Research and task history remain distinct; signing logic is untouched.
    assert len(pool.read_events(case.scope,case.e['scope_epoch']))==1


def test_task_epoch_cas_and_append_only(case):
    r=task(case)
    with pytest.raises(ValueError,match='version conflict'):
        task(case,'attempt',expected_version=0)
    with pytest.raises(ValueError,match='research version conflict'):
        task(case,'attempt',expected_research_version=42)
    with pytest.raises(ValueError,match='scope CAS'):
        task(case,'attempt',scope_epoch='old')
    with sqlite3.connect(pool.db_path(case.scope)) as db:
        for sql in ('UPDATE events SET reason="overwrite"','DELETE FROM events'):
            with pytest.raises(sqlite3.IntegrityError,match='append-only'):
                db.execute(sql)
    assert len(tasks.timeline(case.scope,case.e['scope_epoch']))==1


def test_two_writers_only_one_task_version_wins(case):
    r=task(case)
    p={k:v for k,v in r.items() if k not in ('task','version','recorded_at','schema_version')}
    p.update(action='attempt',expected_version=1)
    def write(i):
        try:
            tasks.apply(case.scope,{**p,'request_id':f'concurrent-{i}'},case.now)
            return 'ok'
        except ValueError as e:
            return str(e)
    with ThreadPoolExecutor(max_workers=2) as ex:
        results=list(ex.map(write,range(2)))
    assert results.count('ok')==1 and any('version conflict' in x for x in results)


def test_period_accounting_wait_check_archive_completion_reopen(case):
    start=case.now-timedelta(seconds=1)
    task(case); check=case.now+timedelta(seconds=2)
    task(case,'wait',disposition_basis='等披露',next_action='核验披露',trigger_key='filing',trigger_condition='新财报发布',check_at=check.isoformat())
    case.now=check+timedelta(seconds=1)
    task(case,'review',decision='archive',disposition_basis='仍无证据',next_action='等新披露')
    task(case,'reopen',sources=[evidence(case,'trigger filing')],trigger_key='filing')
    # Existing watch evidence timestamp is adjusted to the actual research time.
    event={**copy.deepcopy(case.e),'reviewed_at':case.now.isoformat()}
    pool.apply(case.scope,event,case.now)
    snap=tasks.snapshot(case.scope,case.e['scope_epoch'],case.now,start)
    a=snap['accounting']
    assert a==dict(opening_active=0,started=1,reopened=2,completed=1,waiting=1,blocked=0,archived=1,closing_active=0,overdue=0,reconciled=True)
    assert snap['unresolved_total']==0 and snap['parked_history']['archived']==1
    text=tasks.render(snap)
    assert '期初活跃待办' in text and '总未解决 0' in text and '不是公司差' in text


def test_actual_cli_inventory_readonly_and_task_route(case):
    task(case)
    out=case.root/'audit'
    args=[sys.executable,'mt1.py','--scope',str(case.scope)]
    cp=subprocess.run(args+['research-inventory','--out',str(out)],capture_output=True,text=True,timeout=10)
    assert cp.returncode==0,cp.stderr
    assert json.loads((out/'inventory.json').read_text())['company_dispositions_written']==0
    status=case.root/'status.json'
    cp=subprocess.run(args+['research-status','--since',(case.now-timedelta(days=1)).isoformat(),'--out',str(status)],capture_output=True,text=True,timeout=10)
    assert cp.returncode==0,cp.stderr
    assert json.loads(status.read_text())['tasks']['accounting']['closing_active']==1
    assert case.scope.read_bytes()==case.before


def test_saved_new_result_is_not_auto_reopen_but_can_supply_explicit_trigger(case):
    case.put(state='pending',research_outcome='partially_researched',gap_or_exit_basis='缺估值')
    task(case,'triage'); task(case,'archive',disposition_basis='低优先级',next_action='等新来源')
    src=evidence(case,'a new filing saved before explicit reopening')
    case.put(state='pending',research_outcome='partially_researched',gap_or_exit_basis='新原文已保存，待显式重开',sources=[src],request_id='save-new',expected_version=1)
    assert get(case)['status']=='archived'
    task(case,'reopen',sources=[src])
    assert get(case)['status']=='active'


def test_archiving_supplement_does_not_remove_valid_watch_or_plan_binding(case):
    case.put()
    src=evidence(case,'new supplementation question')
    task(case,'reopen',sources=[src])
    task(case,'archive',disposition_basis='补研搁置，不退出证券',next_action='新证据重开')
    scope=load(case.scope)
    assert case.e['code'] in scope['candidates']
    from mt1.scope import filter_plans
    assert filter_plans([dict(code=case.e['code'],scope_epoch=case.e['scope_epoch'],research_event_id='test1')],scope)
    assert scope['research_pool']['episodes'][0]['exit'] is None


def test_direct_admission_counts_real_start_and_completion(case):
    case.put()
    a=tasks.snapshot(case.scope,case.e['scope_epoch'],case.now,case.now-timedelta(seconds=1))['accounting']
    assert a['started']==a['completed']==1 and a['closing_active']==0
