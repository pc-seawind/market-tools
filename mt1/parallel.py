"""MT-1.1 independent discovery and current-data timing diagnostics.

This is a new SHADOW method, not a relaxation of historical.price_features' D5
250-session/PIT contract. 120-session warmup is explicit and never certified PIT.
No ledger, finalization, watchlist or trading writes are made here.
"""
import gzip
import hashlib
import json
import math
import statistics
import subprocess
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from .candidates import day, number, screen

METHOD = {'id':'mt11-parallel-shadow-2','status':'shadow','horizon_sessions':[20,40,60],
          'warmup':120,'value':{'annual_roe':10,'pe_max':25,'pb_max':3},
          'trend':{'return60_min':0.05,'rs60_min':0},
          'reversal':{'drawdown120_max':-0.20},
          'timing':{'extension20_max':0.10,'breakout_volume_min':1.2,'near_ma20_volume_max':1.1},
          'risk_profiles':{'nonfinancial':{'debt_max':70},'financial':{'debt_max':None,'cashflow_filter':False,'requires':'capital_quality_review'}},
          'provenance':'2026-09-11 architecture approval; numerical thresholds exploratory, NOT efficacy-approved'}


def stage(status, *reasons):
    return {'status':status,'reasons':list(reasons)}


def board(code):
    return '北交' if code.endswith('.BJ') else '科创' if code.startswith('688') else '创业' if code.startswith(('300','301')) else '主板'


def financial(rows, asof):
    eligible=[]
    for f in rows:
        try:
            if day(f['end_date'])<=day(f['ann_date'])<day(asof): eligible.append(f)
        except (ValueError,KeyError,TypeError): continue
    def latest(items):
        if not items:return None
        key=max((r['end_date'],r['ann_date']) for r in items)
        selected=[r for r in items if (r['end_date'],r['ann_date'])==key]
        if len({json.dumps(r,sort_keys=True) for r in selected})!=1:return None
        return selected[0]
    current=latest(eligible)
    annual=latest([r for r in eligible if r['end_date'].endswith('1231')])
    if current and (day(asof)-day(current['end_date'])).days>240:current=None
    if annual and (day(asof)-day(annual['end_date'])).days>550:annual=None
    previous_period=str(int(current['end_date'][:4])-1)+current['end_date'][4:] if current else None
    comparable=latest([r for r in eligible if r['end_date']==previous_period])
    positive_base=bool(current and comparable and (number(current.get('eps')) or 0)>0 and (number(comparable.get('eps')) or 0)>0)
    return {'current':current,'annual':annual,'comparable_prior_year':comparable,
            'positive_eps_base':positive_base,'current_period_type':'annual' if current and current['end_date'].endswith('1231') else 'cumulative_interim_not_TTM',
            'roe_basis':'annual_report_only_no_interim_annualization',
            'growth_basis':'latest_cumulative_period_yoy_not_TTM; negative_base_requires_research',
            'pit':'current_vendor_vintage; publication-filtered, NOT historic_revision_certified'}


def features(code,sessions,bars,factors,benchmark):
    """Exact session joins, latest-anchor adjusted OHLC, unadjusted volume."""
    if len(sessions)!=METHOD['warmup'] or sessions!=sorted(set(sessions)):raise ValueError('warmup_calendar_invalid')
    def index(rows):
        out={}
        for r in rows:
            if r.get('ts_code')!=code or r['trade_date']>sessions[-1]:raise ValueError('future_or_foreign_price')
            if r['trade_date'] in out:raise ValueError('duplicate_price')
            out[r['trade_date']]=r
        return out
    ds=index(bars); fs=index(factors)
    def pos(v):
        n=number(v)
        if n is None or n<=0:raise ValueError('invalid_positive_price_volume_factor')
        return n
    anchor=pos(fs[sessions[-1]]['adj_factor']); c=[];h=[];lo=[];v=[]
    for d in sessions:
        r=ds[d];a=pos(fs[d]['adj_factor'])/anchor
        c.append(pos(r['close'])*a);h.append(pos(r['high'])*a);lo.append(pos(r['low'])*a);v.append(pos(r['vol']))
        if not lo[-1]<=c[-1]<=h[-1]:raise ValueError('invalid_ohlc')
    if len({r['trade_date'] for r in benchmark})!=len(benchmark) or any(r['trade_date']>sessions[-1] for r in benchmark):
        raise ValueError('benchmark_future_or_duplicate')
    b={r['trade_date']:pos(r['close']) for r in benchmark}
    if any(d not in b for d in sessions):raise ValueError('benchmark_hole')
    ma20=statistics.mean(c[-20:]);ma60=statistics.mean(c[-60:]);ret=c[-1]/c[-61]-1
    tr=[max(h[i]-lo[i],abs(h[i]-c[i-1]),abs(lo[i]-c[i-1])) for i in range(1,len(c))]
    return {'asof':sessions[-1],'close':c[-1],'ma20':ma20,'ma60':ma60,
        'ma60_5ago':statistics.mean(c[-65:-5]),'return60':ret,'rs60':ret-(b[sessions[-1]]/b[sessions[-61]]-1),
        'extension20':c[-1]/ma20-1,'drawdown120':c[-1]/max(h)-1,
        'previous_high20':max(h[-21:-1]),'today_low':lo[-1],
        'volume_ratio':v[-1]/statistics.mean(v[-21:-1]),'atr20':statistics.mean(tr[-20:]),
        'below_ma60_5sessions':all(c[i]<statistics.mean(c[i-59:i+1]) for i in range(len(c)-5,len(c))),
        'price_basis':'latest-anchor adjusted OHLC; raw volume; CSI300 arithmetic excess return60',
        'warmup_sessions':len(sessions)}


def discover(channel,f,d,t):
    if channel=='VALUE':
        a=f['annual']
        if not a:return stage('unknown','annual_financial_missing_or_conflicting')
        vals=[number(a.get('roe')),number(d.get('pe_ttm')),number(d.get('pb'))]
        if any(v is None for v in vals):return stage('unknown','value_metrics_missing')
        return stage('pass' if vals[0]>=10 and 0<vals[1]<=25 and 0<vals[2]<=3 else 'reject','annual_ROE_PE_PB_shadow_screen')
    if not t:return stage('unknown','technical_panel_missing_or_invalid')
    if channel=='TREND':
        ok=t['close']>t['ma60'] and t['ma60']>t['ma60_5ago'] and t['return60']>0.05 and t['rs60']>0
        return stage('pass' if ok else 'reject','independent_trend_price_discovery_not_business_evidence')
    ok=t['drawdown120']<=-0.20
    return stage('pass' if ok else 'reject','independent_deep_drawdown_not_recovery_evidence')


def risks(stock,d,f,asof):
    if 'ST' in stock.get('name','').upper():return stage('reject','special_treatment')
    if d.get('trade_date')!=asof:return stage('unknown','daily_missing_stale_or_future')
    if number(d.get('turnover_rate')) is None:return stage('unknown','liquidity_unknown')
    if number(d['turnover_rate'])<=0:return stage('reject','no_turnover')
    if not f['current']:return stage('unknown','financial_missing_stale_or_conflicting')
    industry=stock.get('industry','')
    if not industry:return stage('unknown','industry_adapter_missing')
    if industry in ('银行','保险','证券','多元金融'):return stage('unknown','financial_capital_asset_quality_and_major_events_pending')
    debt=number(f['current'].get('debt_to_assets'))
    if debt is None:return stage('unknown','leverage_unknown')
    if debt>70:return stage('reject','nonfinancial_leverage_shadow_limit')
    return stage('unknown','numeric_risk_checks_pass_major_events_tradability_review_pending')


def timing(t,asof,expected):
    if not t or t.get('asof')!=asof or asof!=expected:return stage('unknown','price_missing_future_or_expired')
    if t['extension20']>0.10:return stage('wait','extended_above_MA20_do_not_chase')
    up=t['close']>t['ma60'] and t['ma60']>t['ma60_5ago'] and t['rs60']>0
    breakout=t['close']>t['previous_high20'] and t['volume_ratio']>=1.2
    pullback=t['today_low']<=t['ma20']*1.02 and t['close']>=t['ma20'] and t['volume_ratio']<=1.1
    return stage('trigger' if up and (breakout or pullback) else 'wait',
                 'breakout_confirmed' if up and breakout else 'near_MA20_low_volume' if up and pullback else 'structure_volume_or_relative_strength_not_confirmed')


def actions(t,tm,asof,exit_basis=None):
    # Hypothetical holding branches, never infer holdings or stamp final/BUY.
    held='持有复核'
    if exit_basis in ('thesis_invalidated','major_risk'):held='退出复核'
    elif exit_basis in ('valuation_realized','deadline_due') or (t and t['below_ma60_5sessions']):held='减仓或退出复核'
    return {'qualification':'pending_review','method_status':'shadow','unheld':'研究未完成',
        'held_hypothetical':held,'holding_status':'unknown','final_buy':False,
        'current_timing':tm,'valid_for_close':asof,'expires':'next_completed_session; recompute, do not carry trigger',
        'trigger':({'breakout_close_above':t['previous_high20'],'volume_ratio_at_least':1.2,
                    'near_MA20_diagnostic':t['ma20'],'near_MA20_volume_at_most':1.1,
                    'common':'close>rising MA60, RS60>0, extension20<=10%; risk/evidence/valuation/signoff all pass'} if t else None),
        'cancel': '任一价格/日历/复权缺失或过期；触发结构失效；量能/相对强弱不再满足；估值或经营证据失效',
        'invalidation':'经营逻辑证伪/重大风险优先退出复核；不得要求满足买入门槛',
        'risk_boundary':{'diagnostic_MA60':t['ma60'],'diagnostic_ATR20':t['atr20'],'policy':'非自动止损价；连续5交易日破MA60仅触发复核；需公司风险证据签审'} if t else {'policy':'unknown'},
        'next_review':'下一已完成交易日检查条件；周末补研究；原期限不可覆盖',
        'horizon':'20/40/60交易日向前观察；1—3个月；尚无原始已签审持仓期限'}


def run_parallel(state_dir,panel,out,reviews=None,decision_at=None):
    root=Path(panel);out=Path(out);out.mkdir(parents=True,exist_ok=False)
    source_paths=[Path(__file__).with_name(n) for n in ('parallel.py','parallel_collect.py','parallel_bridge.py','parallel_cycle.py','review_time.py','forward.py')]
    source_hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in source_paths}
    start_head=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip()
    sessions=json.loads((root/'sessions.json').read_text());asof=sessions[-1]
    from .review_time import instant
    now=instant(decision_at) if decision_at else datetime.now(timezone.utc)
    if now>datetime.now(timezone.utc):raise ValueError('future decision time')
    from zoneinfo import ZoneInfo
    local=now.astimezone(ZoneInfo('Asia/Shanghai'))
    manifest=[]
    def read(p):
        raw=p.read_bytes();value=json.loads(raw)
        manifest.append({'path':str(p.resolve()),'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),
                         'fetched_at':value.get('fetched_at','unknown_original_snapshot') if isinstance(value,dict) else 'unknown_original_snapshot',
                         'api':value.get('api') if isinstance(value,dict) else None,
                         'params':value.get('params') if isinstance(value,dict) else None})
        return value
    cal=read(root/'trade_cal-all.json')
    if not 0<=(now-datetime.fromisoformat(cal['fetched_at'])).total_seconds()<=604800:
        raise ValueError('calendar stale/future')
    expected=max(r['cal_date'] for r in cal['rows'] if r['is_open']=='1' and (r['cal_date']<local.strftime('%Y%m%d') or r['cal_date']==local.strftime('%Y%m%d') and local.hour>=15))
    if asof!=expected:raise ValueError('panel not latest completed calendar session')
    if cal['params'].get('end_date')!=local.strftime('%Y%m%d'):raise ValueError('calendar does not cover today')
    review_packets=read(Path(reviews)) if reviews else []
    def packet(code,ch,kind):
        matches=[p for p in review_packets if p.get('code')==code and p.get('channel')==ch and p.get('kind')==kind]
        if len(matches)>1: raise ValueError('duplicate review packet')
        return matches[0] if matches else None
    ds=defaultdict(list);fs=defaultdict(list)
    for d in sessions:
        for name,target in [('daily',ds),('adj_factor',fs)]:
            envelope=read(root/(name+'-'+d+'.json'))
            if envelope['api']!=name or envelope['params'].get('trade_date')!=d or any(r['trade_date']!=d for r in envelope['rows']):
                raise ValueError('panel envelope/date mismatch')
            for r in envelope['rows']:target[r['ts_code']].append(r)
    benchmark_path=root/('index_daily-'+asof+'.json')
    bench=read(benchmark_path if benchmark_path.exists() else root/'index_daily-all.json')['rows']
    sweep=Path(state_dir)/'sweeps'/str(day(asof)); universe=read(sweep/'stock_basic.json')
    basic_rows=read(sweep/'daily_basic.json')
    if len({r['ts_code'] for r in universe})!=len(universe) or len({r['ts_code'] for r in basic_rows})!=len(basic_rows):
        raise ValueError('duplicate universe/daily identity')
    basics={r['ts_code']:r for r in basic_rows};obs=[];records=[]
    for s in universe:
        code=s['ts_code'];p=sweep/('financial-'+code+'.json');raw=read(p) if p.exists() else []
        d=basics.get(code,{})
        obs.append({'stock':s,'daily':d,'financials':raw})
        f=financial(raw if all(r.get('ts_code')==code for r in raw) else [],asof);t=None;err=None
        try:t=features(code,sessions,ds[code],fs[code],bench)
        except (ValueError,KeyError,ZeroDivisionError,IndexError) as e:err=str(e)
        channels={ch:discover(ch,f,d,t) for ch in ('VALUE','TREND','REVERSAL')}
        found=[ch for ch,v in channels.items() if v['status']=='pass']
        risk=risks(s,d,f,asof)
        tm=timing(t,asof,expected)
        evidence={ch:stage('pending',{'VALUE':'1—3月价值修复催化待公司原文核验','TREND':'营收/盈利/订单持续性待原文核验；同比负基数不可当增速','REVERSAL':'经营/供需改善证据待核验，深跌不等于反转'}[ch]) for ch in found}
        vals={ch:stage('pending','financial_PE_PB_ROE_peer_capital_adapter' if s.get('industry') in ('银行','保险','证券','多元金融') else 'earnings_scenarios_peer_comparison_price_justification_required') for ch in found}
        channel_risk={}
        for ch in found:
            for kind,target in [('evidence',evidence),('valuation',vals)]:
                p=packet(code,ch,kind)
                if p:target[ch]=review_gate(p,code,ch,asof,kind,decision_at=now)
            reviewed_risk=review_gate(packet(code,ch,'risk'),code,ch,asof,'risk',decision_at=now)
            # Research cannot override a deterministic risk rejection or missing numerical data.
            numeric_clear=risk['reasons']==['numeric_risk_checks_pass_major_events_tradability_review_pending']
            financial_adapter=risk['reasons']==['financial_capital_asset_quality_and_major_events_pending']
            channel_risk[ch]=reviewed_risk if (numeric_clear or financial_adapter) and reviewed_risk['status'] in ('pass','reject') else risk
        plan=actions(t,tm,asof) if found else None
        if plan:
            readiness={}
            for ch in found:
                blockers=[]
                for kind,v in [('risk',channel_risk[ch]),('evidence',evidence[ch]),('valuation',vals[ch])]:
                    if v['status']!='pass':blockers.append(kind+':'+v['status']+':'+','.join(v['reasons']))
                vp=packet(code,ch,'valuation')
                if vals[ch]['status']=='pass' and t and t['close']>number(vp['price_below']):blockers.append('valuation:price_above_justified_limit')
                if tm['status']!='trigger':blockers.append('timing:'+tm['status'])
                readiness[ch]={'blockers':blockers,'research_conditions_ready':not blockers,'final_blocker':'method_shadow_research_signoff_required'}
            plan['channel_readiness']=readiness
            plan['candidate_origin']='mt11_parallel_shadow'
            rejected=any(channel_risk[ch]['status']=='reject' for ch in found)
            ready=any(v['research_conditions_ready'] for v in readiness.values())
            plan['unheld']='风险否决，不推进新买研究' if rejected else '研究已齐，仅待方法与签审批准' if ready else '研究未完成'
            plan['decision_at']=now.isoformat()
        records.append({'code':code,'name':s['name'],'board':board(code),'industry':s.get('industry'),
            'discovery':channels,'channels':found,'risk':risk,'channel_risk':channel_risk,'evidence':evidence,'valuation':vals,
            'financial':f,'daily':d,'technical':t,'technical_error':err,'timing':tm,
            'plan':plan,
            'downstream_mode':'diagnostic_even_if_risk_unknown_or_reject; not gate_pass'})
    ranked=sorted([r for r in records if r['channels']],key=lambda r:(r['risk']['status']=='reject',r['technical'] is None,
        not (r['financial']['positive_eps_base'] and (number(r['financial']['current'].get('netprofit_yoy')) or 0)>0),
        r['timing']['status']!='trigger',-(r['technical'] or {}).get('rs60',-999),r['code']))
    for i,r in enumerate(ranked,1):r['research_rank']=i
    advancing=[r for r in ranked if not any(v['status']=='reject' for v in r['channel_risk'].values())]
    from .scope import load, codes, counts
    scope=load()
    if scope is not None:
        advancing=[r for r in advancing if r['code'] in codes(scope)]
    batch=advancing[:10]
    old=screen({'asof':str(day(asof)),'universe_size':len(universe),'observations':obs,'data_version':'archived-input'})
    totals={}
    for ch in ('VALUE','TREND','REVERSAL'):
        candidates=[r for r in records if ch in r['channels']]
        totals[ch]={'discovery':dict(Counter(r['discovery'][ch]['status'] for r in records)),
            'discovery_boards':dict(Counter(r['board'] for r in candidates)),
            'risk':dict(Counter(r['channel_risk'][ch]['status'] for r in candidates)),
            'evidence':dict(Counter(r['evidence'][ch]['status'] for r in candidates)),
            'valuation':dict(Counter(r['valuation'][ch]['status'] for r in candidates)),
            'timing_diagnostic':dict(Counter(r['timing']['status'] for r in candidates)),
            'final':{'research_incomplete':sum(not r['plan']['channel_readiness'][ch]['research_conditions_ready'] for r in candidates),
                     'method_approval_pending':sum(r['plan']['channel_readiness'][ch]['research_conditions_ready'] for r in candidates),'BUY':0}}
    stage_distribution={}
    for ch in ('VALUE','TREND','REVERSAL'):
        groups=defaultdict(Counter)
        for r in records:
            stages={'discovery':r['discovery'][ch]}
            if ch in r['channels']:
                stages.update(risk=r['channel_risk'][ch],evidence=r['evidence'][ch],valuation=r['valuation'][ch],timing=r['timing'])
            for name,v in stages.items():
                groups[name+'|'+v['status']+'|board'][r['board']]+=1
                groups[name+'|'+v['status']+'|industry'][r['industry'] or 'unknown']+=1
        stage_distribution[ch]=dict(groups)
    old_codes={r['code'] for r in old['candidates']}; new_codes={r['code'] for r in ranked}
    summary={'stage_distribution':stage_distribution,'comparison':{'added':sorted(new_codes-old_codes),'removed':sorted(old_codes-new_codes)},'asof':asof,'method':METHOD,'generated_at':datetime.now(timezone.utc).isoformat(),'decision_at':now.isoformat(),'universe':len(universe),'channels':totals,
        'tracking_scope':counts(scope),'advancing_research_count':len(advancing),'research_batch_codes':[r['code'] for r in batch],
        'risk_timing_cross':dict(Counter(r['risk']['status']+'|'+r['timing']['status'] for r in ranked)),
        'deduplicated':len(ranked),'multi_channel':sum(len(r['channels'])>1 for r in ranked),
        'old_value_shadow':{'count':len(old['candidates']),'codes':[r['code'] for r in old['candidates']],'complete_data':old['complete_data_count']},
        'ranking_basis':'research priority only: risk rejection last, technical coverage, positive cumulative profit YoY with positive same-period EPS base, timing trigger, RS60, code; NOT expected returns',
        'final_buy':0,'zero_buy_reason':'research_unfinished_and_method_shadow; NOT fully_reviewed_zero',
        'forward_validation':{'entry':'not yet admitted; archive diagnostic cohort only','20_40_60':'pending_observation'},
        'incomplete':['全部公司事件/可交易性核验、逻辑与价格论证、研究签审未完成','新参数有效性未经回测/前瞻验证','旧2024主题档案恢复独立incomplete，不影响本次运行']}
    # Freeze complete inputs, not only mutable external paths. Hash each frozen file.
    for name,payload in [('calendar-input',cal),('financial-input',obs),('price-input',{'sessions':sessions,'daily':ds,'factors':fs,'benchmark':bench}),('funnel',records)]:
        with gzip.open(out/(name+'.json.gz'),'wt') as fp:json.dump(payload,fp,ensure_ascii=False,allow_nan=False)
    (out/'review-input.json').write_text(json.dumps(review_packets,ensure_ascii=False,indent=2))
    summary['source_manifest']=manifest
    summary['frozen_hashes']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in out.glob('*.gz')}
    if any(hashlib.sha256(p.read_bytes()).hexdigest()!=source_hashes[p.name] for p in source_paths):
        raise ValueError('code changed during run; rerun new immutable output')
    summary['git_head']=start_head
    summary['code_hashes']=source_hashes
    (out/'forward-cohort.json').write_text(json.dumps({'created_at':now.isoformat(),'decision_close':asof,
        'method':METHOD,'codes':[r['code'] for r in ranked],'holding_inferred':False,
        'horizons':[{'sessions':h,'status':'not_matured','return':None} for h in (20,40,60)],
        'evaluation_contract':'diagnostic selection cohort, not executed portfolio; future returns require costs, limits, executable next price and independent validation'},ensure_ascii=False,indent=2))
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
    for filename,items in [('discovery-pool.jsonl',ranked),('research-queue.jsonl',advancing),('research-batch.jsonl',batch)]:
        with (out/filename).open('w') as fp:
            for r in items:fp.write(json.dumps(r,ensure_ascii=False)+'\n')
    import csv
    with (out/'stages.csv').open('w') as fp:
        w=csv.writer(fp);w.writerow(['code','name','board','channel','discovery','discovery_reasons','risk','risk_reasons','evidence','valuation','timing','timing_reason','rank'])
        for r in records:
            for ch,v in r['discovery'].items():w.writerow([r['code'],r['name'],r['board'],ch,v['status'],';'.join(v['reasons']),r.get('channel_risk',{}).get(ch,r['risk'])['status'],';'.join(r.get('channel_risk',{}).get(ch,r['risk'])['reasons']),r['evidence'].get(ch,{'status':'not_admitted'})['status'],r['valuation'].get(ch,{'status':'not_admitted'})['status'],r['timing']['status'],';'.join(r['timing']['reasons']),r.get('research_rank','')])
    lines=['**MT-1.1 真实旁路：不是最终推荐**','',f'用户范围 {counts(scope)}；数据日 {asof}；原始扫描池 {len(records)}；去重发现 {len(ranked)}；最终签审未完成，0 BUY 不是研究后全部否决。',
        '|通道|发现通过/拒绝/未知|风险|诊断择时|','|---|---|---|---|']
    for ch,v in totals.items():lines.append(f"|{ch}|{v['discovery']}|{v['risk']}|{v['timing_diagnostic']}|")
    lines+=['','**十只研究交接批次（不是可买池，已移除风险否决）**','|代码/名称|通道|未持有|假设已持有|诊断原因|','|---|---|---|---|---|']
    for r in batch:lines.append(f"|{r['code']} {r['name']}|{','.join(r['channels'])}|{r['plan']['unheld']}|{r['plan']['held_hypothetical']}|{','.join(r['timing']['reasons'])}；{str(r['plan']['channel_readiness'])}|")
    lines+=['','**风险×技术诊断交叉统计（所有trigger都不是可买池）**',str(summary['risk_timing_cross']),'**完整流程**','独立发现 → 共用行业适配风险 → 分通道公司证据 → 估值/盈利情景 → 技术择时 → 两类持仓条件动作 → 研究签审。风险未知或失败的择时仅作诊断，不构成下游通过。',f"旧价值 shadow {len(old['candidates'])} 只是对照，不是总池；新版 VALUE 仅用年度 ROE，不年化半年报。",'','**代表计划卡（真实指标，shadow条件）**']
    reps=[]
    for ch in ('VALUE','TREND','REVERSAL'):
        r=next((r for r in ranked if ch in r['channels'] and r['technical']),None)
        if r and r not in reps:reps.append(r)
    for r in reps:
        lines += [f"\n**{r['name']} {r['code']}｜{','.join(r['channels'])}**",'```json',json.dumps({'technical':r['technical'],'plan':r['plan'],'risk':r['risk'],'financial':r['financial']},ensure_ascii=False,indent=2),'```']
    lines+=['','功能：旁路已跑到条件动作；未完成：公司研究与最终签审。策略：未经有效性验证。历史：2024缺档独立待补。','全名单：stages.csv；全部计划：research-queue.jsonl；原始输入与源hash：summary.json + *-input.json.gz。']
    (out/'report.md').write_text('\n'.join(lines)+'\n')
    return {'out':str(out),'asof':asof,'universe':len(records),'deduplicated':len(ranked),'channels':totals}


def review_gate(packet, code, channel, asof, kind, *, decision_at=None):
    """Explicit company research handoff contract; source files are hash-verified.

    Pure, no promotion/ledger mutation. Reject expired/future/mismatched evidence;
    accepting a packet means evidence recorded, NEVER final BUY authorization.
    """
    if not packet:return stage('pending','research_packet_missing')
    try:
        assert packet['code']==code and packet['channel']==channel and packet['kind']==kind
        from .review_time import review_times
        # Compatibility: absence of decision_at explicitly means historical EOD, NOT now.
        clock=decision_at or str(day(asof))+'T23:59:59.999999+08:00'
        times=review_times(packet,asof,clock)
        assert packet['reviewer']
        assert packet['conclusion'] in ('pass','reject','partial') and packet['reason']
        assert packet['sources']
        from urllib.parse import urlparse
        for s in packet['sources']:
            assert urlparse(s['url']).scheme in ('http','https') and urlparse(s['url']).netloc
            assert hashlib.sha256(Path(s['path']).read_bytes()).hexdigest()==s['sha256']
        if packet['conclusion']=='partial':
            assert packet['remaining_checks'] and packet['facts']
        elif kind=='valuation':
            assert packet['basis'] in ('earnings_scenarios','peer_comparison','asset_value','financial_capital')
            assert packet['assumptions'] and packet['downside_case']
            assert number(packet['price_below']) is not None and number(packet['price_below'])>0
        if kind=='evidence' and packet['conclusion']!='partial':
            from .review_time import instant,CN
            assert packet['milestone'] and 0<(day(packet['milestone_date'])-instant(clock).astimezone(CN).date()).days<=100
        if kind=='risk' and packet['conclusion']!='partial':assert set(packet['checks'])=={'financial','liquidity','major_event','tradability'} and all(v in ('pass','reject') for v in packet['checks'].values())
        if kind=='risk' and packet['conclusion']=='pass':assert all(v=='pass' for v in packet['checks'].values())
    except (KeyError,ValueError,TypeError,AssertionError,OSError):return stage('unknown','review_packet_invalid_future_expired_or_unverified')
    return {**stage(packet['conclusion'],packet['reason']),'reviewer':packet['reviewer'],'times':times,'remaining_checks':packet.get('remaining_checks',[]),'facts':packet.get('facts',{}),'source_hashes':[s['sha256'] for s in packet['sources']]}
