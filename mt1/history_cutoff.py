"""Revision2 additive common-cutoff valuation, never changes/replays orders.

python -m mt1.history_cutoff --source reports/mt12-history-20260912 --out reports/mt12-history-20260912-r2
Reads immutable revision1 artifacts. Costs and execution dates remain frozen.
"""
import argparse
import gzip
import json
import math
import random
from collections import Counter,defaultdict
from pathlib import Path
from statistics import mean
from .history_research import sha,write


def drawdown(values):
    peak=1.;dd=0.
    for x in values:
        peak=max(peak,x);dd=min(dd,x/peak-1)
    return dd


def value_event(t,p,fold):
    """Unit initial event capital; actual fills or exact cutoff mark, never impute.

    Full path = session closes + actual fill opens; no ordering of daily extremes.
    A missing exposed close makes path drawdown unknown even if final mark exists.
    """
    dates=p['dates'];bars=p['bars'];lo=dates.index(fold['test_start']);hi=dates.index(fold['test_end'])
    if not lo<=t['signal_index']<=hi:raise ValueError('signal_outside_OOS')
    ent=t['entry_fill'];ex=t.get('exit_fill',{});c=t['cost_bps']/10000
    r={k:t[k] for k in ('code','fold','group','arm','scenario','cost_bps','episode_id','signal_date')}
    r.update({k:t[k] for k in ('market','industry','industry_current_vintage','volatility','regime') if k in t})
    r.update(entry_identity={k:ent.get(k) for k in ('status','index','date','price')},cutoff=dates[hi],original_status=t['status'],entry_status=ent['status'],exit_status=ex.get('status','not_requested'),
             valuation_status='unknown',entry_event_value=None,capital_opportunity_value=None,
             realized_component=None,unrealized_component=None,unpaid_sell_cost_sensitivity=None,
             full_path_drawdown=None,observed_only_drawdown=None,path_missing_dates=[],path=[],
             entry_fee_rate=c,actual_sell_fee_rate=c if t['status']=='closed' else 0,
             cash_interest=0,fill_constraints={'entry_skipped':ent.get('skipped',[]),'exit_skipped':ex.get('skipped',[])})
    if ent['status']!='filled':
        # Unknown is not cash. Verified not-entered is cash only for the
        # capital-opportunity denominator, not a 0-return entered trade.
        unknown=ent['status']=='unknown' or t['status']=='unknown'
        if not unknown and ent['status'] in ('pending','cancelled'):
            r.update(valuation_status='known_no_entry_cash',capital_opportunity_value=0.,realized_component=0.,unrealized_component=0.,full_path_drawdown=0.,observed_only_drawdown=0.,unpaid_sell_cost_sensitivity=0.)
            r['path']=[dict(date=d,kind='cash_no_entry',wealth=1.) for d in dates[lo:hi+1]]
        else:r['valuation_status']='unknown_entry_execution'
        return r
    start=ent['index']
    if not t['signal_index']<start<=hi or not math.isfinite(ent['price']) or ent['price']<=0:
        raise ValueError('entry_outside_OOS_or_invalid')
    if bars[start] is None or not math.isclose(ent['price'],bars[start]['open'],abs_tol=1e-10):
        raise ValueError('entry_price_mismatch')
    qty=1/(ent['price']*(1+c));end=None;proceeds=None
    if t['status']=='closed':
        if ex.get('status')!='filled':raise ValueError('closed_without_exit')
        end=ex['index']
        if not start<end<=hi or not t['exit_signal_date']<dates[end]:raise ValueError('exit_outside_OOS_or_Tplus1')
        if bars[end] is None or not math.isclose(ex['price'],bars[end]['open'],abs_tol=1e-10):raise ValueError('exit_price_mismatch')
        proceeds=qty*ex['price']*(1-c)
        if not math.isclose(proceeds-1,t['net_price_return'],abs_tol=1e-12):raise ValueError('realized_return_mismatch')
    elif ex.get('status')=='filled':raise ValueError('filled_exit_not_closed')
    path=[]
    for i in range(lo,hi+1):
        date=dates[i]
        if i<start:path.append(dict(date=date,kind='cash_before_entry',wealth=1.));continue
        if i==start:path.append(dict(date=date,kind='entry_open_after_cost',wealth=qty*ent['price']))
        if end is not None and i>=end:
            path.append(dict(date=date,kind='exit_open_after_cost' if i==end else 'cash_after_exit',wealth=proceeds));continue
        b=bars[i]
        if b is None or not math.isfinite(b['close']) or b['close']<=0:
            path.append(dict(date=date,kind='missing_exposed_close',wealth=None));r['path_missing_dates'].append(date)
        else:path.append(dict(date=date,kind='held_close_mark',wealth=qty*b['close']))
    vals=[1.]+[x['wealth'] for x in path if x['wealth'] is not None]
    r.update(buy_fee_paid_initial_capital=c/(1+c),sell_fee_paid_initial_capital=qty*ex['price']*c if end is not None else 0.,path=path,observed_only_drawdown=drawdown(vals),full_path_drawdown=drawdown(vals) if not r['path_missing_dates'] else None)
    if end is not None:
        v=proceeds-1;r.update(valuation_status='realized_then_cash',entry_event_value=v,capital_opportunity_value=v,
                             realized_component=v,unrealized_component=0.,unpaid_sell_cost_sensitivity=v)
    elif t['status']=='unknown' or ex.get('status')=='unknown':
        # Even if a mark could be computed, unresolved order execution means
        # we cannot assert whether assets or cash were held at cutoff.
        r.update(valuation_status='unknown_execution_after_entry',full_path_drawdown=None,observed_only_drawdown=None,path_assertion='hypothetical_asset_holding_after_unresolved_execution')
    elif path[-1]['wealth'] is None:
        r.update(valuation_status='unknown_cutoff_price')
    else:
        v=path[-1]['wealth']-1
        r.update(valuation_status='unrealized_at_exact_cutoff',entry_event_value=v,capital_opportunity_value=v,
                 realized_component=0.,unrealized_component=v,unpaid_sell_cost_sensitivity=(1+v)*(1-c)-1)
    return r


def summarize(rows,key):
    vals=[r[key] for r in rows if r[key] is not None]
    dd=[r['full_path_drawdown'] for r in rows if r['full_path_drawdown'] is not None]
    sense=[r['unpaid_sell_cost_sensitivity'] for r in rows if r['unpaid_sell_cost_sensitivity'] is not None]
    realized=[r['realized_component'] for r in rows if r['realized_component'] is not None]
    unrealized=[r['unrealized_component'] for r in rows if r['unrealized_component'] is not None]
    return dict(total=len(rows),known=len(vals),unknown=len(rows)-len(vals),
        verified_no_entry=sum(r['valuation_status'] in ('known_no_entry_cash','no_signal_cash') for r in rows),
        execution_or_price_or_observation_unknown=sum('unknown' in r['valuation_status'] or 'unobservable' in r['valuation_status'] for r in rows),mean_known=mean(vals) if vals else None,
        mean_over_full_denominator=mean(vals) if vals and len(vals)==len(rows) else None,
        worst_known=min(vals) if vals else None,mean_realized_component_known=mean(realized) if realized else None,
        mean_unrealized_component_known=mean(unrealized) if unrealized else None,
        mean_hypothetical_sell_cost_sensitivity=mean(sense) if sense else None,sensitivity_coverage=len(sense),
        path_drawdown_known=len(dd),path_drawdown_unknown=len(rows)-len(dd),mean_full_path_drawdown=mean(dd) if dd else None,
        worst_full_path_drawdown=min(dd) if dd else None,status_counts=dict(Counter(r['valuation_status'] for r in rows)))


def crossed_interval(pairs,field,seed=1201,n=2000):
    known=[p for p in pairs if p[field] is not None]
    codes=sorted({p['code'] for p in known});folds=sorted({p['fold'] for p in known})
    rng=random.Random(seed);samples=[]
    if known:
        for _ in range(n):
            cw=Counter(rng.choices(codes,k=len(codes)));fw=Counter(rng.choices(folds,k=len(folds)))
            w=[cw[p['code']]*fw[p['fold']] for p in known];den=sum(w)
            if den:samples.append(sum(x*p[field] for x,p in zip(w,known))/den)
    samples.sort()
    return dict(total=len(pairs),known=len(known),unknown=len(pairs)-len(known),stock_clusters=len(codes),time_clusters=len(folds),
        mean_known=mean(p[field] for p in known) if known else None,
        mean_over_full_denominator=mean(p[field] for p in known) if known and len(known)==len(pairs) else None,
        descriptive_95_interval=[samples[int(.025*(len(samples)-1))],samples[int(.975*(len(samples)-1))]] if samples else None,
        seed=seed,replicates=n,valid_resamples=len(samples),method='crossed_stock_fold_bootstrap_complete_observed_pairs_NOT_iid_NOT_significance_proof')


def compare_pairs(rows,old_arm,new_arm,group,key):
    lookup=defaultdict(dict)
    for r in rows:
        if r['group']==group:lookup[r['episode_id'] if group=='A' else (r['code'],r['fold'])][r['arm']]=r
    pairs=[]
    for ident,arms in lookup.items():
        a,b=arms.get(old_arm),arms.get(new_arm)
        sample=a or b
        same_entry=(group!='A' or (a and b and a.get('entry_identity')==b.get('entry_identity')))
        diff=lambda field: b[field]-a[field] if same_entry and a and b and a[field] is not None and b[field] is not None else None
        pairs.append(dict(same_entry=same_entry,identity=ident,code=sample['code'],fold=sample['fold'],delta=diff(key),
            drawdown_delta=diff('full_path_drawdown'),hypothetical_sell_cost_delta=diff('unpaid_sell_cost_sensitivity'),
            old_status=a['valuation_status'] if a else 'missing_arm',new_status=b['valuation_status'] if b else 'missing_arm'))
    return dict(pairs=pairs,valuation=crossed_interval(pairs,'delta'),drawdown=crossed_interval(pairs,'drawdown_delta'),
                hypothetical_sell_cost=crossed_interval(pairs,'hypothetical_sell_cost_delta'),
                attribution='same_frozen_entry_episode' if group=='A' else 'common_code_fold_capital_opportunity_NOT_same_entry_causal_effect')


def load_panel(source,code):
    # Prefer committed compressed snapshot, independent of mutable intermediates.
    p=source/f'panel-{code}.json.gz'
    manifest=json.loads((source/'derived-archives.json').read_text())[p.name]
    if sha(p)!=manifest['compressed_sha256']:raise ValueError('panel_archive_changed')
    raw=gzip.decompress(p.read_bytes())
    import hashlib
    if hashlib.sha256(raw).hexdigest()!=manifest['uncompressed_sha256']:raise ValueError('panel_bytes_changed')
    return json.loads(raw)


def build(source):
    original=json.loads((source/'full-results.json').read_text());spec=json.loads((source/'frozen-contract.json').read_text())
    panels={c:load_panel(source,c) for c in spec['codes']}
    folds={(f['code'],f['fold']):f for f in original['folds']};audits={(a['code'],a['fold']):a for a in original['audit']}
    events=[]
    for t in original['trades']:
        events.append(value_event(t,panels[t['code']],folds[t['code'],t['fold']]))
    opportunities=[]
    for scenario in spec['execution_scenarios']:
        for cost in spec['costs_bps_per_side']:
            ts=[r for r in events if r['scenario']==scenario and r['cost_bps']==cost and r['group']=='B']
            lookup={(r['code'],r['fold'],r['arm']):r for r in ts}
            for (code,fold),f in folds.items():
                audit=audits[code,fold]['opportunities']
                for arm in spec['trial_contract']['group_B']['entries']:
                    r=lookup.get((code,fold,arm))
                    if r is None:
                        unknown=bool(audit.get('unknown_or_warmup',0))
                        r=dict(code=code,fold=fold,group='B',arm=arm,scenario=scenario,cost_bps=cost,episode_id=None,
                            valuation_status='no_signal_unobservable_sessions' if unknown else 'no_signal_cash',
                            entry_event_value=None,capital_opportunity_value=None if unknown else 0.,
                            realized_component=None if unknown else 0.,unrealized_component=None if unknown else 0.,
                            full_path_drawdown=None if unknown else 0.,unpaid_sell_cost_sensitivity=None if unknown else 0.)
                    opportunities.append({**r,'opportunity_sessions':f['test_sessions'],
                        'unknown_or_warmup_sessions':audit.get('unknown_or_warmup',0),
                        'trigger_sessions':audit.get(arm+'|trigger',0),'source_opportunities':audit})
    summaries={};pairs={}
    for scenario in spec['execution_scenarios']:
        for cost in spec['costs_bps_per_side']:
            key=f'{scenario}|{cost}'
            es=[e for e in events if e['scenario']==scenario and e['cost_bps']==cost]
            bs=[e for e in opportunities if e['scenario']==scenario and e['cost_bps']==cost]
            for group in ('A','B'):
                arms=spec['trial_contract']['group_A']['exits'] if group=='A' else spec['trial_contract']['group_B']['entries']
                for arm in arms:
                    summaries[f'{key}|{group}|{arm}|entered_events']=summarize([e for e in es if e['group']==group and e['arm']==arm],'entry_event_value')
                    if group=='B':summaries[f'{key}|B|{arm}|capital_opportunities']=summarize([e for e in bs if e['arm']==arm],'capital_opportunity_value')
            for arm in ('structure_failure','structure_failure_atr'):
                pairs[f'{key}|A|{arm}']=compare_pairs(es,'old_ma60_5',arm,'A','entry_event_value')
            for arm in ('breakout','pullback'):
                pairs[f'{key}|B|{arm}']=compare_pairs(bs,'old_trigger',arm,'B','capital_opportunity_value')
    strata=defaultdict(list)
    for e in events:
        for field in ('code','fold','market','industry','industry_current_vintage','volatility','regime'):
            if field in e:strata[f"{e['scenario']}|{e['cost_bps']}|{e['group']}|{e['arm']}|{field}|{e[field]}"].append(e)
    return dict(strata={k:summarize(v,'entry_event_value') for k,v in strata.items()},method='common_fold_cutoff_all_entered_event_valuation_v1',events=events,B_opportunities=opportunities,
        summary=summaries,paired=pairs,limitations=['not_PIT_selection','not_funded_portfolio','not_cash_dividend_total_return',
        'unknown_not_zero_or_dropped','exposed_missing_close_invalidates_full_path_drawdown','no_intraday_high_low_path','small_correlated_clusters'],
        input_results_sha256=sha(source/'full-results.json'))


def pct(x):return 'unknown' if x is None else f'{100*x:.4f}%'


def report(r):
    lines=['# Revision2：共同fold截止全事件估值','',
        '**修正结论：全71个A入场episode的共同截止估值中，结构＋ATR相对旧退出为负；不能沿用只看双闭合的较好倾向。**',
        '这不是重跑策略或修改参数，而是只读revision1订单，补齐退出资金持有现金与未退出持仓的共同终点。',
        '已退出：已实现净回报＋其后零息现金。未退出：精确fold末收盘估值，只扣已发生入场费。未成交/未知另列，不伪造卖出。',
        '路径为fold内所有收盘及真实入/出场开盘成交估值点；前后现金纳入。不是日内高低排序回撤；持仓期间任何缺价则全路径回撤unknown。','',
        '|基准15bps组/规则/口径|总数/有值/未知|共同截止均值|已实现贡献均值|未实现贡献均值|最差截止估值|全路径平均/最差回撤|回撤已知/未知|',
        '|---|---|---:|---:|---:|---:|---|---|']
    for k,s in r['summary'].items():
        if k.startswith('open_price_limit_base_v1|15|'):
            lines.append(f"|{'/'.join(k.split('|')[2:])}|{s['total']}/{s['known']}/{s['unknown']}|{pct(s['mean_known'])}|{pct(s['mean_realized_component_known'])}|{pct(s['mean_unrealized_component_known'])}|{pct(s['worst_known'])}|{pct(s['mean_full_path_drawdown'])}/{pct(s['worst_full_path_drawdown'])}|{s['path_drawdown_known']}/{s['path_drawdown_unknown']}|")
    lines+=['','均值若有unknown只是已知子集均值；全分母均值字段保持null，不能把缺失填0。B机会口径含全部108个股票×fold；无信号且完整可观测者为零息现金，不可观测者unknown。',
        'B各臂触发日期不同；共同截止/机会分母差不是同入场因果效应，也不是资金组合收益。','',
        '|基准15bps配对（新版−旧版）|全/已知/未知|估值差|股票×fold描述性95%区间|全路径回撤差（正较好）|',
        '|---|---|---:|---|---:|']
    for k,p in r['paired'].items():
        if k.startswith('open_price_limit_base_v1|15|'):
            s=p['valuation'];ci=s['descriptive_95_interval']
            lines.append(f"|{'/'.join(k.split('|')[2:])}|{s['total']}/{s['known']}/{s['unknown']}|{pct(s['mean_known'])}|{'至'.join(map(pct,ci)) if ci else 'unknown'}|{pct(p['drawdown']['mean_known'])}|")
    lines+=['','revision1双闭合35/71的ATR差约+1.47个百分点；本次71/71约−0.165个百分点。两者回答不同条件问题，不能选择有利的子集结论。',
        '固定种子1201、2000次股票×OOS折交叉重抽样，完整保留未知配对。区间仅描述性，少簇/市场共同冲击/当前vintage偏差不因此消失。','',
        '## 全部成本/执行情景及未退出假设卖出费敏感性','',
        '|情景/成本/组/臂/分母|已知/总数|共同截止均值|未退出假设卖出费后的均值（非成交）|','|---|---|---:|---:|']
    for k,s in r['summary'].items():lines.append(f"|{k}|{s['known']}/{s['total']}|{pct(s['mean_known'])}|{pct(s['mean_hypothetical_sell_cost_sensitivity'])}|")
    lines+=['','敏感性仅从仍持仓的终点估值扣假设卖出费，不创建exit、不改成交日期、不叫已实现净收益；已退出不重复收费。',
        '事件记录保留完整财富路径、已实现/未实现分量、不能成交及缺价原因。事件统计不是有资金约束的组合收益。']
    return '\n'.join(lines)+'\n'


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--source',type=Path,required=True);ap.add_argument('--out',type=Path,required=True)
    args=ap.parse_args();source=args.source.resolve();out=args.out.resolve()
    if source==out or source in out.parents:raise ValueError('must_use_independent_sibling_output')
    cfg=json.loads((out/'analysis-contract.json').read_text())
    if sha(source/'full-results.json')!=cfg['input_results_sha256'] or sha(source/'frozen-contract.json')!=cfg['trial_contract_sha256']:raise ValueError('frozen_source_changed')
    before=json.loads((out/'revision1-before.json').read_text())
    if any(sha(source/k)!=h for k,h in before.items()):raise ValueError('revision1_artifact_changed')
    r=build(source);write(out/'common-cutoff-results.json',r)
    (out/'common-cutoff-report.md').write_text(report(r))
    write(out/'run.json',dict(source_results_sha256=sha(source/'full-results.json'),analysis_contract_sha256=sha(out/'analysis-contract.json'),
        implementation_sha256=sha(Path(__file__)),results_sha256=sha(out/'common-cutoff-results.json'),revision1_preserved_files=len(before)))
    print(json.dumps({k:v for k,v in r['summary'].items() if k.startswith('open_price_limit_base_v1|15|A|')},ensure_ascii=False))


if __name__=='__main__':main()
