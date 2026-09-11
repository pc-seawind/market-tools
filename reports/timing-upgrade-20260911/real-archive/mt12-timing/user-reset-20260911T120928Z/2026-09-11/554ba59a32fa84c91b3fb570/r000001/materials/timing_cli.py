"""Only MT-1.2 CLI: collect / observe / compare / weekly. Shadow-only artifacts.

No cron, production DB, watchlist, thesis or research admission mutation.
"""
import argparse
import csv
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from .scope import load, codes, DEFAULT
from .timing import contract, digest, evaluate, CONTRACT_PATH, instant
from .timing_experiment import compare
from .longitudinal import archive, weekly_index, manifests

HERE = Path(__file__).resolve().parents[1]


def write_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = value if isinstance(value,bytes) else json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False).encode()
    with path.open('xb') as f: f.write(raw)


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def protected_hashes(scope_path):
    paths = [Path(scope_path), HERE/'watchlist.yaml', HERE/'watchlist_changes.jsonl', HERE/'recommendations.jsonl']
    paths += list((Path(scope_path).parents[1]/'thesis').rglob('*.yaml'))
    paths += list((HERE/'watchlist').rglob('*'))
    paths += list((HERE/'.cron_state/mt1').glob('plans.db*'))
    paths += list((HERE/'.cron_state/mt1').rglob('forward-cohort.json'))
    return {str(p.resolve()):file_hash(p) for p in paths if p.is_file()}


def collect(scope_path, out):
    scope = load(scope_path)
    root = Path(out); root.mkdir(parents=True,exist_ok=False)
    before = protected_hashes(scope_path)
    started = datetime.now(timezone.utc)
    inputs=[]; errors=[]
    def fetch(api, **params):
        name = api+'-'+digest(params)[:12]+'.csv'
        cmd=[sys.executable,str(HERE/'tushare.py'),api,'--csv']+[f'{k}={v}' for k,v in params.items()]
        p=subprocess.run(cmd,capture_output=True,timeout=45)
        fetched=datetime.now(timezone.utc).isoformat()
        write_new(root/name,p.stdout)
        # Do not archive environment/credentials or command stderr secrets.
        inputs.append({'name':name,'path':str((root/name).resolve()),'sha256':file_hash(root/name),'api':api,'params':params,'fetched_at':fetched,'representation':'exact_provider_CLI_stdout_bytes_not_HTTP_body','returncode':p.returncode})
        if p.returncode: raise ValueError(api+'_failed_exit_'+str(p.returncode))
        rows=list(csv.DictReader(p.stdout.decode().splitlines()))
        if not rows: raise ValueError(api+'_empty')
        return rows
    today=started.astimezone(ZoneInfo('Asia/Shanghai')).date()
    start=(today-timedelta(days=260)).strftime('%Y%m%d'); end=today.strftime('%Y%m%d')
    gates={}
    for market, api, tz, hour in [('CN','trade_cal','Asia/Shanghai',15),('HK','hk_tradecal','Asia/Hong_Kong',17),('US','us_tradecal','America/New_York',17)]:
        try:
            raw=fetch(api, start_date=start, end_date=end, **({'exchange':'SSE'} if market=='CN' else {}))
            ds=sorted({r['cal_date'] for r in raw if r['is_open']=='1' and instant(datetime.strptime(r['cal_date'],'%Y%m%d').replace(hour=hour,tzinfo=ZoneInfo(tz)).isoformat())<=started})[-120:]
            gates[market]={'sessions':[datetime.strptime(d,'%Y%m%d').date().isoformat() for d in ds], 'close_suffix':f'T{hour:02d}:00:00'+ ('+08:00' if market!='US' else '-04:00'), 'timezone':tz, 'hour':hour, 'verified':len(ds)==120}
        except Exception as e:
            gates[market]={'sessions':[],'verified':False,'error':str(e)}
            errors.append({'market':market,'calendar':str(e)})
    bench=None
    try:
        raw=fetch('index_daily',ts_code='000300.SH',start_date=start,end_date=end)
        bench={'id':'000300.SH','market':'CN','price_basis':'compatible_price_return','fetched_at':datetime.now(timezone.utc).isoformat(), 'bars':[{'date':datetime.strptime(r['trade_date'],'%Y%m%d').date().isoformat(),'close':float(r['close'])} for r in raw]}
    except Exception as e: errors.append({'broad_CN':str(e)})
    panels=[]
    for code,item in {**scope['candidates'],**scope['recommendations'],**scope['holdings']}.items():
        market=item.get('market') or ('HK' if code.endswith('.HK') else 'CN' if code.endswith(('.SZ','.SH','.BJ')) else 'US')
        g=gates[market]; sessions=g['sessions']
        p={'code':code,'name':item.get('name'),'market':market,'sessions':sessions,'calendar_verified':g['verified'],
           'expected_date':sessions[-1] if sessions else None,'bars':[],'adjustment':'unknown','basis_id':None,'benchmarks':{},'channel':item.get('channel','TREND')}
        try:
            if market=='CN':
                bars=fetch('daily',ts_code=code,start_date=start,end_date=end)
                factors=fetch('adj_factor',ts_code=code,start_date=start,end_date=end)
                factor={r['trade_date']:r['adj_factor'] for r in factors}
                p['bars']=[{'date':datetime.strptime(r['trade_date'],'%Y%m%d').date().isoformat(),
                            'close_at':datetime.strptime(r['trade_date'],'%Y%m%d').date().isoformat()+'T15:00:00+08:00',
                            **{k:float(r[k]) for k in ('open','high','low','close','vol')},'factor':float(factor[r['trade_date']])} for r in sorted(bars,key=lambda b:b['trade_date']) if datetime.strptime(r['trade_date'],'%Y%m%d').date().isoformat() in sessions]
                p.update(adjustment='vendor_factor_verified',basis_id='tushare_daily_times_adj_factor_constant_unit_current_vintage')
                if bench: p['benchmarks']['broad']=bench
            elif market=='HK':
                # Same public endpoint as existing quote_sources, but retain EXACT
                # body. qfq alone cannot establish an immutable factor anchor.
                import urllib.request
                symbol='hk'+code.split('.')[0]
                url=f'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={symbol},day,,,150,qfq'
                with urllib.request.urlopen(url,timeout=20) as response: raw=response.read()
                name=code+'.http.json'; write_new(root/name,raw)
                inputs.append({'name':name,'path':str((root/name).resolve()),'sha256':file_hash(root/name),'url':url,'fetched_at':datetime.now(timezone.utc).isoformat(),'representation':'exact_HTTP_body'})
                data=json.loads(raw)['data'][symbol]
                p['bars']=[{'date':r[0],'open':float(r[1]),'close':float(r[2]),'high':float(r[3]),'low':float(r[4]),'vol':float(r[5])} for r in data.get('qfqday',data.get('day',[])) if r[0] in sessions]
                p['data_gap']='HK_factor_anchor_and_industry_benchmark_unknown_no_risk_price'
            else:
                p['data_gap']='US_price_factor_and_industry_adapter_not_collected'
        except Exception as e:
            p['collection_error']=str(e); errors.append({'code':code,'error':str(e)})
        p['fetched_at']=datetime.now(timezone.utc).isoformat(); panels.append(p)
    asof=datetime.now(timezone.utc).isoformat()
    result={'source_kind':'real_current_readonly_collection','scope_epoch':scope['epoch'],'scope_codes':sorted(codes(scope)),
            'asof':asof,'started_at':started.isoformat(),'panels':panels,'inputs':inputs,'errors':errors,'markets':gates,'entry_episodes':[],
            'contract_hash':digest(contract()),'protected_before':before,'protected_after':protected_hashes(scope_path)}
    result['protected_unchanged']=result['protected_before']==result['protected_after']
    write_new(root/'bundle.json',result)
    return {'bundle':str(root/'bundle.json'),'sha256':file_hash(root/'bundle.json'),'errors':errors,'protected_unchanged':result['protected_unchanged']}


def report(result):
    details=[]
    lines=['**MT-1.2 shadow 技术附表｜不是正式买卖指令**',
           '仅放入原日报第三部分；行情播报、趋势预测与观点对照保持不变。成本/买入日未知，不计算个人盈亏。',
           '|代码/名称|行情日期/收盘|旧入场 / 旧退出|新入场 / 持仓风险|结构 / ATR20 / 保护线|缺口|',
           '|---|---|---|---|---|---|']
    for r in result['cards']:
        entry='; '.join(k+':'+v['status'] for k,v in r['entry'].items() if isinstance(v,dict)) or r['entry'].get('status')
        lines.append(f"|{r['code']} {r.get('name','')}|{r.get('quote_date','unknown')} / {r.get('close','unknown')}|{r['old_entry']['status']} / {r['old_exit']['status']}|{entry} / {r['risk']['status']}|{r.get('structure_price')} / {r.get('atr20')} / {r.get('atr_stop')}|{'; '.join(r['gaps']) or '入场仍需研究签审'}|")
        if r.get('state'):
            m=r['state']['monitor']
            details.append(f"\n{r['code']}：前向首次观测 {m['observed_at']}（不是买入日期）；风险依据 {m['structure_start']}—{m['structure_known_date']} 已完成结构低点；内部 raw×factor，表中折回当日口径。宽基/行业 RS：{r['rs']}。")
    lines += details
    lines += ['', '缺历史/复权时风险价阻断；unknown 不当作安全或负分。港股不套沪深300；无美股持仓不宣称美股覆盖。',
              '20/40/60 交易日窗口尚未成熟；首次监控不回填历史最大浮盈，不改旧计划 EXIT 或期限。']
    return '\n'.join(lines)+'\n'


def _observe(bundle_path, scope_path, root, previous=None):
    scope=load(scope_path); raw=Path(bundle_path).read_bytes(); bundle=json.loads(raw)
    if bundle['scope_epoch']!=scope['epoch'] or set(bundle['scope_codes'])!=codes(scope):
        raise ValueError('scope_epoch_or_codes_changed_recollect_no_legacy_fallback')
    if len(bundle['panels'])!=len(codes(scope)) or {p['code'] for p in bundle['panels']}!=codes(scope):
        raise ValueError('missing_duplicate_or_foreign_panel')
    if bundle.get('contract_hash')!=digest(contract()): raise ValueError('collection_contract_mismatch')
    before=protected_hashes(scope_path)
    prev={}
    original_events={}
    if previous:
        manifest=json.loads(Path(previous).read_text())
        source=Path(previous).parent/manifest['result']['path']
        if file_hash(source)!=manifest['result']['sha256']: raise ValueError('previous_snapshot_hash_mismatch')
        prior=json.loads(source.read_text())
        if prior['scope_epoch']!=scope['epoch']: raise ValueError('previous_scope_mismatch')
        prev={r['code']:r['state'] for r in prior['cards']}
        original_events={e['origin_id']:e for e in manifest.get('events',[])}
    cfg=contract()
    run_id=digest([raw.decode(),file_hash(scope_path),file_hash(previous) if previous else None,cfg])[:24]
    for _,path,m in manifests(root):
        if m['job']=='mt12-timing' and m['run_id']==run_id:
            if file_hash(Path(path).parent/m['result']['path'])!=m['result']['sha256']: raise ValueError('existing_archive_corrupt')
            for material in m['materials']:
                if material.get('blob') and file_hash(Path(path).parent/material['blob'])!=material['sha256']: raise ValueError('existing_material_corrupt')
            return {'manifest':path,'idempotent':True,'sha256':file_hash(path)}
    if not previous and any(m['scope_epoch']==scope['epoch'] and m['job']=='mt12-timing' for _,_,m in manifests(root)):
        raise ValueError('previous_manifest_required_to_continue_existing_episode')
    now=datetime.now(timezone.utc).isoformat()
    cards=[]; events=[]
    items={**scope['candidates'],**scope['recommendations'],**scope['holdings']}
    for p in bundle['panels']:
        r=evaluate(p,asof=now,previous=prev.get(p['code']),channel=p.get('channel','TREND'))
        r.update(name=items[p['code']].get('name'),holding_status='confirmed' if p['code'] in scope['holdings'] else 'not_held',source_files=bundle.get('inputs',[]))
        # Preserve quote visibility even when adjustment blocks technical prices.
        if r['status']=='blocked' and p.get('bars'):
            b=p['bars'][-1];r.update(quote_date=b['date'],close=b['close'])
        cards.append(r)
        origin=(r.get('state') or {}).get('monitor',{})
        origin_date=original_events.get(p['code']+':'+cfg['method'],{}).get('original_date',origin.get('origin_date',p.get('expected_date')))
        events.append({'kind':'mt12-monitor','origin_id':p['code']+':'+cfg['method'], 'status':'blocked' if r['status']=='blocked' else 'not_matured',
                       'original_judgment':'前向技术观察，不是用户建仓/推荐起点','original_date':origin_date,
                       'reference_price':None, 'verification_target':'20/40/60_completed_sessions_not_personal_pnl',
                       'next_review_date':str(datetime.fromisoformat(now).date()+timedelta(days=1)),
                       'observed_sessions':(r.get('state') or {}).get('observed_sessions',0),'latest_risk':r['risk']})
    result={'status':'partial' if any(r['status']=='blocked' for r in cards) else 'ok', 'scope_epoch':scope['epoch'],'method':cfg,'run_id':run_id,
            'execution_started_at':now,'cards':cards,'markets':bundle.get('markets',{}),'protected_before':before,'protected_after':protected_hashes(scope_path),
            'incomplete':['historical_PIT_universe_and_executable_sessions_unknown','20_40_60_not_matured','no_active_promotion_or_VPS_wiring']}
    result['protected_unchanged']=before==result['protected_after']
    materials=[{'name':'bundle.json','content':raw},{'name':'scope.json','path':str(scope_path)},{'name':'experiment-contract.json','path':str(CONTRACT_PATH)},
               {'name':'report.md','content':report(result)}]
    for source in (Path(__file__), Path(__file__).with_name('timing.py'), Path(__file__).with_name('timing_experiment.py')):
        materials.append({'name':source.name,'path':str(source)})
    for inp in bundle.get('inputs',[]):
        if file_hash(inp['path'])!=inp['sha256']: raise ValueError('source_bytes_changed')
        materials.append({'name':inp['name'],'path':inp['path'],'fetched_at':inp['fetched_at'],'representation':inp['representation']})
    receipt=archive(root=root,job='mt12-timing',run_id=run_id,trade_date=now[:10],scope_epoch=scope['epoch'],result=result,materials=materials,events=events,
                    execution={'execution_id':'mt12-'+run_id,'started_at':now,'completed_at':datetime.now(timezone.utc).isoformat(),'status':'partial' if result['status']=='partial' else 'ok'})
    return receipt


def observe(bundle_path, scope_path, root, previous=None):
    import fcntl
    Path(root).mkdir(parents=True,exist_ok=True)
    with (Path(root)/'.timing.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        return _observe(bundle_path,scope_path,root,previous)


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='cmd',required=True)
    c=sub.add_parser('collect');c.add_argument('--scope',default=DEFAULT);c.add_argument('--out',required=True)
    c=sub.add_parser('observe');c.add_argument('--scope',default=DEFAULT);c.add_argument('--bundle',required=True);c.add_argument('--root',required=True);c.add_argument('--previous')
    c=sub.add_parser('compare');c.add_argument('--bundle',required=True);c.add_argument('--out',required=True)
    c=sub.add_parser('weekly');c.add_argument('--root',required=True);c.add_argument('--asof',required=True);c.add_argument('--epoch',required=True);c.add_argument('--out',required=True)
    a=p.parse_args()
    if a.cmd=='collect': r=collect(a.scope,a.out)
    elif a.cmd=='observe': r=observe(a.bundle,a.scope,a.root,a.previous)
    elif a.cmd=='compare':
        r=compare(json.loads(Path(a.bundle).read_text()));write_new(a.out,r);r={'out':a.out,'sha256':file_hash(a.out),'summary':r['summary']}
    else:
        r=weekly_index(a.root,asof=a.asof,current_epoch=a.epoch);write_new(a.out,r);r={'out':a.out,'sha256':file_hash(a.out)}
    print(json.dumps(r,ensure_ascii=False,indent=2))

if __name__=='__main__': main()
