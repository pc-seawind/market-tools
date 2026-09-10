"""Four report phases; stable checkpoints, bounded stages, no automatic decisions."""
import fcntl
import json
import subprocess
from datetime import datetime, timezone, date
from pathlib import Path
from .calendar import gate, check_fresh
from .data import HERE, atomic_json, cn_calendar, collect_universe, foreign_calendar
from .candidates import screen
from .plans import legacy_plan, reduce_plan, eligible, deadline_errors
from .store import Store, digest
from .backtest import audit


def migrate(store, rec_path, thesis_dir):
    import yaml
    latest={}; invalid=[]
    if Path(rec_path).exists():
        for n,line in enumerate(Path(rec_path).read_text().splitlines(),1):
            if not line.strip(): continue
            try:
                r=json.loads(line)
                entity='legacy:rec:'+digest([str(rec_path),n,r])
                store.apply(entity,0,entity,r,'原始推荐逐行保留，非成交',lambda old,p:{'raw':p})
                code=r.get('code') or r.get('ts_code')
                if code: latest[code]=r
            except (ValueError,TypeError) as e: invalid.append({'line':n,'error':str(e)})
    for p in sorted(Path(thesis_dir).glob('*.yaml')):
        try:
            raw=p.read_text(); r=yaml.safe_load(raw)
            r=json.loads(json.dumps(r,default=lambda v:v.isoformat() if isinstance(v,(date,datetime)) else str(v)))
            entity='legacy:thesis:'+digest([str(p),raw])
            store.apply(entity,0,entity,{'path':str(p),'raw':raw},'保留thesis原文，不改源文件',lambda old,v:v)
            if isinstance(r,dict): latest.setdefault(r.get('ticker') or r.get('code') or p.stem,r)
        except Exception as e: invalid.append({'path':str(p),'error':str(e)})
    imported=0
    for code,r in latest.items():
        p=legacy_plan(r,'legacy-cold-start',code)
        # Unknown episode history is not rewritten into a fake historical BUY.
        if r.get('action')=='EXIT': p.update(state='EXIT',held_direction='EXIT')
        entity='plan:'+p['plan_id']
        if store.latest(entity): continue
        store.apply(entity,0,entity,p,'冷启动待复核；原始日期/成本未知',lambda old, patch: reduce_plan(old, patch, legacy_import=True)); imported+=1
    return {'imported':imported,'source_symbols':len(latest),'errors':invalid}


def run(phase, state_dir, investment_dir, now=None, fixture=None, collect=False, max_stocks=0, run_id=None):
    now=now or datetime.now(timezone.utc)
    from zoneinfo import ZoneInfo
    today=now.astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat()
    run_id=run_id or f'{today}-{phase}'
    if not run_id or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in run_id):
        raise ValueError('invalid run_id')
    state_dir=Path(state_dir); state_dir.mkdir(parents=True,exist_ok=True)
    with (state_dir/'pipeline.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        directory=state_dir/'runs'/run_id; directory.mkdir(parents=True,exist_ok=True)
        manifest_path=directory/'manifest.json'
        signature=digest([phase,today,fixture,collect,max_stocks,str(investment_dir)])
        manifest=json.loads(manifest_path.read_text()) if manifest_path.exists() else {'stages':{},'signature':signature}
        if manifest['signature']!=signature: raise ValueError('run_id reused with different inputs')
        errors=[]
        def stage(name, fn):
            item=manifest['stages'].get(name)
            if item and item['status']=='done': return json.loads(Path(item['path']).read_text())
            manifest['stages'][name]={'status':'running','started_at':datetime.now(timezone.utc).isoformat()}
            atomic_json(manifest_path,manifest)
            try:
                value=fn(); path=directory/(name+'.json'); atomic_json(path,value)
                manifest['stages'][name]={'status':'done','path':str(path),'hash':digest(value)}
            except Exception as e:
                value=None; errors.append({'stage':name,'error':str(e)})
                manifest['stages'][name]={'status':'failed','error':str(e)}
            atomic_json(manifest_path,manifest)
            return value
        calendars=(fixture or {}).get('calendars',{})
        if phase not in ('saturday','sunday') and 'CN' not in calendars:
            cal=stage('calendar',lambda:cn_calendar(now))
            if cal: calendars={**calendars,'CN':cal}
        if phase not in ('saturday','sunday') and fixture is None:
            for market in ('HK','US'):
                cal=stage('calendar_'+market,lambda m=market:foreign_calendar(now,m))
                if cal: calendars={**calendars,market:cal}
        gates={m:gate(calendars.get(m,{}),m,phase,now) for m in ('CN','HK','US')}
        store=Store(state_dir/'plans.db')
        try:
            migration=stage('migration',lambda:migrate(store,HERE/'recommendations.jsonl' if fixture is None else Path(investment_dir)/'recommendations.jsonl',Path(investment_dir)/'thesis'))
            expected=gates['CN']['expected_date']; recap=None; pool=None
            if phase in ('morning','evening') and gates['CN']['allowed']:
                def recap_stage():
                    if fixture is not None:
                        value=fixture.get('recap')
                    else:
                        path=Path('/tmp')/f'evening_recap_{expected}.json'
                        if phase=='evening' and collect and not path.exists():
                            subprocess.run([str(HERE/'evening_recap_data.sh'),'--out',str(path)],check=True,timeout=2400)
                        value=json.loads(path.read_text()) if path.exists() else None
                    if not value: raise ValueError('recap_missing; morning never substitutes current-date recompute for prior session')
                    actual=str(value.get('meta',{}).get('trade_date',''))
                    if len(actual)==8: actual=f'{actual[:4]}-{actual[4:6]}-{actual[6:]}'
                    if not check_fresh(actual,expected): raise ValueError('recap_stale_or_unknown_trade_date')
                    if value.get('meta',{}).get('fresh') is not True or value.get('meta',{}).get('errors'):
                        raise ValueError('recap_data_quality_failed')
                    return value
                recap=stage('recap',recap_stage)
                if fixture is not None and fixture.get('universe'):
                    pool=stage('quality_value',lambda:screen(fixture['universe']))
                elif collect and phase=='evening':
                    pool=stage('quality_value',lambda:screen(collect_universe(expected,state_dir/'universe',max_stocks)))
            if gates['CN']['reason'] == 'calendar_missing_invalid_or_stale':
                errors.append({'stage':'calendar_gate','error':'CN calendar unavailable; fail-closed'})
            plans=store.all()
            due=[p['plan_id'] for p in plans if p['state']!='EXIT' and (not p.get('review_due') or p['review_due']<=today or deadline_errors(p, date.fromisoformat(today)))]
            # Initial report has no new final conclusions. Agent's evidence-reviewed
            # event file is applied by a distinct validated CLI command.
            previous=[]
            for previous_path in (state_dir/'runs').glob('*/report.json'):
                if previous_path.parent != directory:
                    previous.append(previous_path)
            previous.sort(key=lambda p:p.stat().st_mtime)
            prior=json.loads(previous[-1].read_text()).get('plans',[]) if previous else []
            before={p['plan_id']:p for p in prior}
            changes=[{'plan_id':p['plan_id'],'before':before.get(p['plan_id']), 'after':p}
                     for p in plans if before.get(p['plan_id'])!=p]
            result={'version':'MT-1.0','run_id':run_id,'phase':phase,'asof':today,'gates':gates,
                    'migration':migration,'plans':plans,'review_due':due,'plan_changes':changes,
                    'raw_recap':recap,'quality_value':pool,'errors':errors,
                    'backtest_readiness':audit({}), 'methods':store.all('method:'),
                    'previous_saturday':None,
                    'research_status':'not_fetched' if phase=='sunday' else 'not_requested',
                    'final_watchlist_candidates':[], 'sync':'dry_run_only',
                    'note':'原计划待证据审查，不自动判定维持/升级/退出；未持有与已有持仓分别研究'}
            if phase=='sunday':
                records=sorted((Path(investment_dir)/'reference/medium-term-reviews').glob('*-saturday*.md'))
                result['previous_saturday']=str(records[-1]) if records else None
                if not records: result['errors'].append({'stage':'sunday','error':'saturday_record_missing'})
            # Re-running after a failed stage emits another immutable report, never overwrites history.
            report_dir=Path(investment_dir)/'reference/medium-term-reviews'; report_dir.mkdir(parents=True,exist_ok=True)
            report=report_dir/f'{today}-{phase}-{datetime.now(timezone.utc).strftime("%H%M%S%f")}.md'
            lines=['**MT-1.0 运行数据｜待证据复核（非最终投资建议）**','',
                   '|标的|未持有者方向|已有持仓方向|复核日|持仓确认|', '|---|---|---|---|---|']
            for p in plans:
                lines.append(f"|{p['code']}|{p['unheld_direction']}|{p['held_direction']}|{p.get('review_due') or '未知'}|{p['holding_status']}|")
            lines+=['',f'运行 ID：`{run_id}`；待复核 {len(due)} 项。',
                    f'日历门禁：`{json.dumps(gates,ensure_ascii=False)}`',
                    '周日联网状态：'+result['research_status'],
                    '本报告只完成数据编排，证据研究/最终结论未完成。',
                    '异常：'+json.dumps(result['errors'],ensure_ascii=False),
                    '完整数据：`'+str(directory/'report.json')+'`']
            with report.open('x') as f: f.write('\n'.join(lines)+'\n')
            result['report_path']=str(report); atomic_json(directory/'report.json',result)
            return result
        finally: store.close()
