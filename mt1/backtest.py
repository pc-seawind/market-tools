"""Paired replay of point-in-time signal tapes, NOT historical channel reconstruction.

Input signal must be archived at decision time; bars provide explicit fillability,
next-open total-return-adjusted prices, delisting settlement and benchmark marks.
Fixed equal-capital sleeves: simultaneous overlaps consume separate sleeves; no
reinvestment after exit. No missing-bar interpolation or fabricated delisting price.
"""
from datetime import datetime
import math
import statistics

REQUIREMENTS = ('pit_financials','pit_industry','historical_universe','delisted_included',
                'archived_channel_signals','next_open_execution','adjustment_consistent',
                'suspension_limit_flags','fees_slippage','benchmark','oos_split')


def audit(data):
    meta=data.get('provenance',{})
    blockers=[k for k in REQUIREMENTS if not (isinstance(meta.get(k),dict) and
              meta[k].get('verified') is True and meta[k].get('artifact_hash'))]
    return {'exact_channel_reconstruction':False, 'status':'unsupported' if blockers else 'signal_tape_replay_only',
            'blockers':blockers,'promotion':'shadow',
            'limitations':['复放调用方提供的已归档信号，不重新计算三通道；证据hash仍需审计',
                           '固定等额独立资金槽；重叠样本不当独立事件；退出现金不再投资',
                           '财报修订/历史成分/退市缺失时拒绝计算精确收益']}


def max_drawdown(curve):
    peak=curve[0]; worst=0
    for v in curve:
        peak=max(peak,v); worst=min(worst,v/peak-1)
    return worst


def replay(data):
    result=audit(data)
    if result['blockers']:
        return {**result,'metrics':None}
    bars={(r['code'],r['date']):r for r in data['bars']}
    sessions=data['sessions']
    if sessions!=sorted(set(sessions)): raise ValueError('sessions must be unique sorted')
    fee=float(data['fee_rate']); slip=float(data['slippage_rate'])
    if not all(math.isfinite(x) and 0<=x<.1 for x in (fee,slip)): raise ValueError('invalid costs')
    samples=[]; omitted=[]
    def fill(code,start,side):
        for i in range(start,len(sessions)):
            b=bars.get((code,sessions[i]))
            if b is None: raise ValueError('missing bar (suspension must be explicit)')
            if side=='sell' and b.get('delisted'):
                if b.get('settlement') is None: raise ValueError('delisting settlement unknown')
                return i,float(b['settlement'])
            if b.get(side+'_fillable') is True:
                price=float(b['open'])
                if not math.isfinite(price) or price<=0: raise ValueError('invalid fill price')
                return i,price
        raise ValueError('no subsequent executable session')
    seen=set()
    for sig in data.get('signals',[]):
        if sig['id'] in seen: raise ValueError('duplicate signal id')
        seen.add(sig['id'])
        if sig.get('channel') not in ('VALUE','TREND','REVERSAL'): raise ValueError('unknown channel')
        for horizon in (20,40,60):
            try:
                decision=sessions.index(sig['date'])
                available=datetime.fromisoformat(sig['available_at']); decision_at=datetime.fromisoformat(sig['decision_at'])
                if not available.tzinfo or not decision_at.tzinfo or available>decision_at or decision_at.date().isoformat()!=sig['date']: raise ValueError('future or undated signal')
                entry,price=fill(sig['code'],decision+1,'buy')
                target=entry+horizon
                if target>=len(sessions): raise ValueError('horizon incomplete')
                end,hold_price=fill(sig['code'],target,'sell')
                exit_signal=sig.get('exit_date')
                if exit_signal:
                    exit_available=datetime.fromisoformat(sig['exit_available_at'])
                    exit_decision=datetime.fromisoformat(sig['exit_decision_at'])
                    if not exit_available.tzinfo or not exit_decision.tzinfo or exit_available>exit_decision or exit_decision.date().isoformat()!=exit_signal:
                        raise ValueError('future or undated exit signal')
                exit_target=max(entry+1,sessions.index(exit_signal)+1) if exit_signal else end
                if exit_signal and sessions.index(exit_signal)<entry: raise ValueError('exit precedes entry')
                actual,exit_price=fill(sig['code'],min(end,exit_target),'sell')
                if actual>end: actual,exit_price=end,hold_price
                paid=price*(1+fee+slip)
                hold_value=hold_price*(1-fee-slip)/paid
                exit_value=exit_price*(1-fee-slip)/paid
                curve_hold={}; curve_exit={}
                for i in range(entry,end+1):
                    b=bars.get((sig['code'],sessions[i]))
                    if b is None: raise ValueError('missing valuation mark')
                    mark=float(b['close'])/paid
                    if not math.isfinite(mark) or mark<0: raise ValueError('invalid valuation mark')
                    curve_hold[sessions[i]]=hold_value if i==end else mark
                    curve_exit[sessions[i]]=exit_value if i>=actual else mark
                benchmark=data['benchmark']
                bench=float(benchmark[sessions[end]]['close'])/float(benchmark[sessions[entry]]['open'])-1
                samples.append({'signal_id':sig['id'],'code':sig['code'],'channel':sig['channel'],'horizon':horizon,
                    'entry':sessions[entry],'end':sessions[end], 'exit':sessions[actual],
                    'hold_return':hold_value-1,'exit_return':exit_value-1,'paired_delta':exit_value-hold_value,
                    'benchmark_return':bench,'hold_excess':hold_value-1-bench,'exit_excess':exit_value-1-bench,
                    'hold_curve':curve_hold,'exit_curve':curve_exit,'hold_occupied_days':end-entry+1,
                    'exit_occupied_days':actual-entry+1, 'hold_turnover':(price+hold_price)/paid, 'exit_turnover':(price+exit_price)/paid, 'split':sig.get('split','unknown')})
            except (ValueError,KeyError,IndexError,TypeError) as e:
                omitted.append({'id':sig['id'],'horizon':horizon,'reason':str(e)})
    metrics={}
    for h in (20,40,60):
        group=[s for s in samples if s['horizon']==h]
        if not group: continue
        dates=sorted({d for s in group for d in s['hold_curve']})
        m={'paired_samples':len(group),'unique_stocks':len({s['code'] for s in group}),
           'unique_entry_dates':len({s['entry'] for s in group}),'independent_samples':'unknown_overlap_not_removed'}
        for strategy in ('hold','exit'):
            # All sleeves exist from outset; idle and exited sleeves remain cash.
            curve=[1.0]
            for d in dates:
                values=[]
                for s in group:
                    values.append(1 if d<s['entry'] else (1+s[strategy+'_return'] if d>s['end'] else s[strategy+'_curve'][d]))
                curve.append(statistics.mean(values))
            returns=sorted(s[strategy+'_return'] for s in group)
            tail=returns[:max(1,math.ceil(len(returns)*.05))]
            m[strategy]={'return':curve[-1]-1,'benchmark_excess':statistics.mean(s[strategy+'_excess'] for s in group),
                'portfolio_max_drawdown':max_drawdown(curve),'tail_cvar_5pct':statistics.mean(tail),
                'gross_turnover':statistics.mean(s[strategy+'_turnover'] for s in group),
                'capital_occupancy':sum(s[strategy+'_occupied_days'] for s in group)/(len(group)*len(dates))}
        m['paired_exit_minus_hold']=statistics.mean(s['paired_delta'] for s in group)
        metrics[str(h)]=m
    return {**result,'metrics':metrics,'samples':samples,'omitted':omitted,'oos_pass':False,
            'note':'样本外检验/独立样本审计未通过自动验收，任何结果均维持shadow'}
