"""MT14 historical comparison: unique collect/run/verify CLI; never production writes.

Original timing.step is the historical pure engine. The live fetched_at and quote
validators are NOT bypassed or monkeypatched. Retrospective decision_at is not
claimed as a historical source acquisition timestamp.
"""
import argparse
import copy
import gzip
import hashlib
import json
from pathlib import Path
from .data import collect, read, write, sha, verify_inputs, panels, seal_bytes
from .report import summarize, render
from ..candidates import screen
from ..timing import step, digest
from ..history_research import next_open as cn_open
from ..hk_history import next_open as hk_open


def close_at(d,market='CN'):
    return d[:4]+'-'+d[4:6]+'-'+d[6:] + ('T16:10:00+08:00' if market=='HK' else 'T15:00:00+08:00')


def drawdown(values):
    peak=1.;worst=0.
    for v in values:
        peak=max(peak,v);worst=min(worst,v/peak-1)
    return worst


def price_value(panel,index,h,cost):
    """Fixed endpoint, no nearest bar; next-session close + h complete sessions."""
    bars=panel['bars'];start=index+1;end=start+h
    if end>=len(bars):return dict(status='not_matured',net=None,gross=None,drawdown=None)
    path=bars[start:end+1]
    if any(b is None for b in path):return dict(status='unknown_price_path',net=None,gross=None,drawdown=None)
    price=path[0]['close'];wealth=[b['close']/(price*(1+cost)) for b in path]
    wealth[-1]*=1-cost
    return dict(status='price_observed_not_fill',net=wealth[-1]-1,gross=path[-1]['close']/price-1,
                drawdown=drawdown([1.]+wealth),entry_date=bars[start]['date'],end_date=bars[end]['date'],
                buy_fee_initial=cost/(1+cost),sell_fee_initial=path[-1]['close']/(price*(1+cost))*cost,
                path_sha256=digest([(b['date'],b['close']) for b in path]))


def cash():return dict(status='unselected_cash',net=0.,gross=0.,drawdown=0.)


def unknown(reason):return dict(status=reason,net=None,gross=None,drawdown=None)


def rows_for(base,panel,i,selected,status,cfg):
    rows=[]
    for h in cfg['horizons']:
        for mult in cfg['cost_multipliers']:
            c=cfg['per_side_bps'][panel['market']]*mult/10000
            val=price_value(panel,i,h,c) if selected else cash() if status=='known' else unknown(status)
            rows.append(dict(**base,selected=selected,horizon=h,cost_multiplier=mult,per_side_bps=c*10000,**val))
    return rows


def fundamental(ps,data,cfg):
    outcomes=[];decisions=[]
    for code in cfg['fundamental_codes']:
        p=ps[code];f=data[code];daily={r['trade_date']:r for r in f['daily']}
        if len(daily)!=len(f['daily']):raise ValueError('duplicate_daily_basic')
        stock=p['evidence'][0]['current_listing']
        for i,date in enumerate(p['dates']):
            if not cfg['evaluation_start']<=date<=cfg['evaluation_end']:continue
            if i+1<len(p['dates']) and p['dates'][i+1][:6]==date[:6]:continue
            usable_names=[n for n in f['names'] if n.get('start_date') and n['start_date']<=date and (not n.get('end_date') or date<=n['end_date']) and (not n.get('ann_date') or n['ann_date']<date)]
            name=max(usable_names,key=lambda n:n['start_date'])['name'] if usable_names else stock['name']
            obs=dict(stock={**stock,'name':name},daily=daily.get(date,{}),financials=f['financials'])
            snap=dict(asof=date,universe_size=1,observations=[obs],data_version=digest(obs))
            outputs={a:screen(snap,cfg['fundamental'][a]) for a in ('old','new')}
            if outputs['old']!=screen(snap):raise ValueError('default_screen_not_equivalent')
            for arm,r in outputs.items():
                selected=bool(r['candidates']);reasons=[x for e in r['excluded'] for x in e['reasons']]
                missing=any(x.startswith(('missing_','no_public_','stale_')) for x in reasons)
                base=dict(category='fundamental',arm=arm,code=code,market=p['market'],date=date,identity=code+'|'+date)
                outcomes+=rows_for(base,p,i,selected,'unknown_fundamental_input' if missing else 'known',cfg)
                decisions.append(dict(**base,selected=selected,reasons=reasons,screen=r,history_name_available=bool(usable_names),source_snapshot_sha256=digest(snap)))
    return outcomes,decisions


def warm_panel(p,warm):
    """Vendor adj_factor units preserved. Warmup has no executable qualifications."""
    if p['market']!='CN' or not warm.get('dates'):return copy.deepcopy(p)
    d=warm.get(p['code'],{});daily={r['trade_date']:r for r in d.get('daily',[])};fac={r['trade_date']:r for r in d.get('adj_factor',[])}
    bs=[];ev=[]
    for date in warm['dates']:
        e=dict(date=date,suspension=[],limit=None,availability='warmup_only_not_execution');ev.append(e)
        b,f=daily.get(date),fac.get(date)
        if not b or not f or not b.get('vol') or b['vol']<=0:bs.append(None);continue
        factor=float(f['adj_factor']);raw={k:float(b[k]) for k in ('open','high','low','close')}
        bs.append(dict(date=date,vol=float(b['vol']),factor=factor,raw=raw,close_at=close_at(date),open_at=close_at(date).replace('15:00','09:30'),**{k:v*factor for k,v in raw.items()}))
    q=copy.deepcopy(p);q['dates']=warm['dates']+p['dates'];q['bars']=bs+p['bars'];q['evidence']=ev+p['evidence']
    for i,b in enumerate(q['bars']):
        if b is not None:b['index']=i
    return q


def historical_action(rows,state,held,cfg):
    """Same pure MT13 action mapping and unheld-risk reset, not live-input validation.

    Equivalence tested against unmodified action_loop.decide on real-valid fixture
    panels. Acquisition dates remain in input manifest, never historic 'observed'.
    """
    s,r=step(rows,state,cfg,channel='TREND',observed_at=rows[-1]['close_at'])
    risk=bool(r['risk']['original_trigger'])
    if not held and state and risk and not r['risk']['reasons'] and rows[-1]['date']>state['last_date']:
        s,r=step(rows,None,cfg,channel='TREND',observed_at=rows[-1]['close_at']);risk=bool(r['risk']['original_trigger'])
    entry=any(v.get('status')=='trigger' for v in r['entry'].values())
    action=('SELL' if held else 'WAIT') if risk else 'BUY' if entry else 'HOLD' if held else 'WAIT'
    return s,r,action


def frozen_exit_action(bar,state,held):
    """MT13 exit_only economic branch: entry data gaps never veto frozen SELL.

    Identity/price source hashes were sealed by verify_inputs. No new ATR or stop
    is invented. Without a demonstrated breach we cannot claim HOLD.
    """
    if bar is None or not state or not state.get('monitor'):
        return 'DATA_BLOCKED',None
    m=state['monitor'];reasons=[]
    if bar['close']<m['structure_low']:reasons.append('initial_structure_invalidated')
    if m.get('atr_stop') is not None and bar['close']<m['atr_stop']:
        reasons.append('ATR_trailing_close_breach_frozen_previous_line')
    if m.get('risk_trigger'):reasons=m['risk_trigger']['reasons']
    if not reasons:return 'DATA_BLOCKED',None
    return ('SELL' if held else 'WAIT'),dict(events=[],risk=dict(original_trigger=dict(reasons=reasons),reasons=reasons))


def execution_value(p,trade,h,c):
    ent=trade.get('entry');start=ent['index'] if ent else None
    if start is None:return unknown(trade['status']) if 'unknown' in trade['status'] or trade['status']=='pending' else cash()
    end=start+h
    if end>=len(p['bars']):return unknown('not_matured')
    if trade.get('unknown_index') is not None and trade['unknown_index']<=end:return unknown('unknown_execution_or_state_path')
    ex=trade.get('exit');exit_i=ex['index'] if ex and ex['index']<=end else None
    wealth=[];price=ent['price'];gross=None
    for i in range(start,end+1):
        if i==start:wealth.append(1/(1+c))
        if exit_i is not None and i>=exit_i:
            gross=ex['price']/price-1;wealth.append((1+gross)*(1-c)/(1+c));continue
        b=p['bars'][i]
        if b is None:return unknown('unknown_exposed_price_path')
        gross=b['close']/price-1;wealth.append((1+gross)/(1+c))
    # Still-open valuation pays only actual entry scenario cost, not a fake exit.
    return dict(status='closed_then_cash' if exit_i is not None else 'open_mark_not_closed',net=wealth[-1]-1,gross=gross,
                drawdown=drawdown([1.]+wealth),entry_date=p['dates'][start],end_date=p['dates'][end],
                exit_date=p['dates'][exit_i] if exit_i is not None else None,
                hypothetical_sell_cost_net=wealth[-1]-1 if exit_i is not None else wealth[-1]*(1-c)-1,
                path_sha256=digest(wealth))


def account_path(p,trades,decisions,cost,capital):
    """One cash-funded capital slot, no overlapping investment or invented holdings."""
    entries={t['entry']['index']:t for t in trades if t.get('entry')}
    exits={t['exit']['index']:t for t in trades if t.get('exit')}
    if len(entries)!=sum(bool(t.get('entry')) for t in trades):raise ValueError('overlapping_entries')
    uncertain_indices=[i for i,d in enumerate(p['dates']) if any(x['date']==d and x['execution_uncertain'] for x in decisions)]
    poison=min(uncertain_indices,default=len(p['dates']))
    for t in trades:
        if t.get('unknown_index') is not None:poison=min(poison,t['unknown_index'])
    balance=float(capital);units=0.;values=[]
    for i,date in enumerate(p['dates']):
        if i in exits:
            if units<=0:raise ValueError('sell_without_funded_position')
            balance=units*exits[i]['exit']['price']*(1-cost);units=0.
        if i in entries:
            if units or balance<=0:raise ValueError('capital_double_allocation')
            units=balance/(entries[i]['entry']['price']*(1+cost));balance=0.
        b=p['bars'][i]
        value=None if i>=poison or (units and b is None) else balance+units*b['close'] if units else balance
        values.append(dict(date=date,wealth=value,units=units,cash=balance,status='unknown' if value is None else 'occupied' if units else 'cash'))
    return values


def account_window(path,i,h):
    start=i+1;end=start+h
    if end>=len(path):return unknown('not_matured')
    rows=path[start:end+1]
    if any(r['wealth'] is None for r in rows):return unknown('unknown_capital_path')
    base=rows[0]['wealth'];values=[r['wealth']/base for r in rows]
    return dict(status='funded_capital_window',net=values[-1]-1,gross=None,drawdown=drawdown([1.]+values),
                entry_date=rows[0]['date'],end_date=rows[-1]['date'],start_wealth=base,end_wealth=rows[-1]['wealth'])


def technical(ps,warm,cfg):
    # Original MT13 transition/recovery functions are reused, not mocked.
    from ..action_loop import transition, TERMINAL
    from ..action_recovery import ensure,renew,expire
    outcomes=[];decisions=[];trades=[];ledgers=[];accounts=[]
    for code in cfg['technical_codes']:
        p=warm_panel(ps[code],warm);bars=p['bars'];dates=p['dates'];index={d:i for i,d in enumerate(dates)}
        for arm in ('old','new'):
            policy=cfg['technical'][arm];tcfg=policy['timing'];state=None
            s={'positions':{},'transitions':[]};key=arm+'|'+code;ledger={};local=[];last_gap=-1;uncertain=False
            for i,date in enumerate(dates):
                if bars[i] is None:last_gap=i
                if not cfg['evaluation_start']<=date<=cfg['evaluation_end']:continue
                base=dict(category='technical_price',arm=arm,code=code,market=p['market'],date=date,identity=code+'|'+date)
                at=bars[i]['close_at'] if bars[i] else close_at(date,p['market']);previous=state
                good=i-last_gap>=tcfg['warmup'] and i>=tcfg['warmup']
                if state and state['last_date']!=dates[i-1]:good=False
                r=None;held=key in s['positions']
                if good:
                    rows=bars[max(last_gap+1,i-119):i+1]
                    state,r,action=historical_action(rows,state,held,tcfg)
                    state['condition_history']=state['condition_history'][-1:]
                else:
                    action,r=frozen_exit_action(bars[i],state,held)
                    if held:
                        t=s['positions'][key]
                        if t.get('unknown_index') is None:t['unknown_index']=i
                risk=bool(r and r['risk']['original_trigger'])
                # Match observe order: current decision first; historical eligible
                # earlier opens next; today's risk then cancels unexecuted entries.
                for order in ledger.values():
                    if (good or risk) and order['side']=='SELL':
                        order['risk_reconfirmation']=dict(active=risk,date=date,known_at=at,source_sha256=digest(r))
                    renew(s,order,at)
                    if order['execution_status'] in TERMINAL:continue
                    attempt=ensure(order);after=index[attempt['after_session']]
                    end=min(i+1,after+policy['order_ttl_sessions']+1)
                    pos=s['positions'].get(key)
                    adapter=cn_open if p['market']=='CN' else hk_open
                    model='open_price_limit_base_v1' if p['market']=='CN' else 'hk_next_open_v1'
                    f=adapter(p,after,end,order['side'].lower(),model,pos['entry']['index'] if pos else None)
                    if f['status']=='filled':
                        if f['index']<=after:raise ValueError('same_session_execution_forbidden')
                        if order['side']=='BUY':
                            if pos:
                                transition(s,order,'cancelled','one_virtual_lot_already_held',at);continue
                            order['trade'].update(entry=f,status='open');s['positions'][key]=order['trade']
                        else:
                            if not pos:
                                transition(s,order,'blocked','no_virtual_position_real_holding_not_seeded',at);continue
                            pos.update(exit=f,status='closed');del s['positions'][key];state=None
                        order['fill']=f;attempt.update(status='filled',ended_at=at,fill_sha256=digest(f))
                        transition(s,order,'filled','historical_daily_estimate_NOT_observed_quote',at)
                    else:
                        transition(s,order,'blocked' if f['status']=='unknown' else 'pending',f.get('reason','await_later_open'),at)
                        if f['status']=='unknown':
                            uncertain=True
                            if order['side']=='BUY':order['trade'].update(status='unknown_execution',unknown_index=i)
                            elif pos and pos.get('unknown_index') is None:pos['unknown_index']=i
                    if good and order['execution_status'] not in TERMINAL and i-after>=policy['order_ttl_sessions']:
                        expire(s,order,at,date)
                        if order['side']=='BUY' and order['trade']['status']=='pending':order['trade']['status']='expired_unfilled_cash'
                held=key in s['positions']
                if risk:
                    action='SELL' if held else 'WAIT'
                    for order in ledger.values():
                        if order['side']=='BUY' and order['execution_status'] not in TERMINAL:
                            transition(s,order,'cancelled','risk_overrides_unexecuted_BUY',at)
                            if order['trade']['status']=='pending':order['trade']['status']='risk_cancelled_cash'
                elif action in ('WAIT','HOLD'):action='HOLD' if held else 'WAIT'
                if action in ('BUY','SELL'):
                    episode=date
                    if action=='SELL':
                        episode=(previous or {}).get('action_exit_episode') or ((r or {}).get('risk',{}).get('original_trigger') or {}).get('date') or date
                        if state is not None:state['action_exit_episode']=episode
                    sid=digest([arm,code,action,episode])
                    active=[o for o in ledger.values() if o['side']==action and o['execution_status'] not in TERMINAL]
                    if sid not in ledger and not active:
                        order=dict(signal_id=sid,position_key=key,side=action,signal_date=date,triggered_at=at,execution_status='pending',execution_reason='await_later_legal_open',history=[],episode=episode)
                        ledger[sid]=order;ensure(order)
                        if action=='BUY':
                            if held:transition(s,order,'cancelled','add_observation_one_virtual_lot_already_held',at)
                            else:
                                t=dict(code=code,market=p['market'],arm=arm,signal_index=i,date=date,identity=code+'|'+date,status='pending',entry=None,exit=None)
                                local.append(t);order['trade']=t
                        elif not held:transition(s,order,'blocked','no_virtual_position_real_holding_not_seeded',at)
                selected=action=='BUY'
                outcomes+=rows_for(base,p,i,selected,'known' if good else 'unknown_warmup_or_history_gap',cfg)
                decisions.append(dict(**base,action=action,held=held,execution_uncertain=uncertain,
                                      events=r['events'] if r else [],risk=r['risk'] if r else None,state_sha256=digest(state) if state else None))
            by_signal={t['date']:t for t in local};ds=[d for d in decisions if d['code']==code and d['arm']==arm]
            paths={}
            for mult in cfg['cost_multipliers']:
                paths[mult]=account_path(p,local,ds,cfg['per_side_bps'][p['market']]*mult/10000,cfg['capital_slot'])
                accounts.append(dict(code=code,arm=arm,cost_multiplier=mult,capital_slot=cfg['capital_slot'],path=paths[mult]))
            for d in ds:
                t=by_signal.get(d['date']);selected=bool(t and (t.get('entry') or t['status'] in ('pending','unknown_execution')))
                for h in cfg['horizons']:
                    for mult in cfg['cost_multipliers']:
                        c=cfg['per_side_bps'][p['market']]*mult/10000
                        val=execution_value(p,t,h,c) if t else unknown('unknown_execution_state') if d['execution_uncertain'] or d['action']=='DATA_BLOCKED' else unknown('occupied_slot_see_capital_table') if d['held'] else cash()
                        base=dict(arm=arm,code=code,market=p['market'],date=d['date'],identity=d['identity'],horizon=h,cost_multiplier=mult,per_side_bps=c*10000)
                        outcomes.append(dict(category='technical_execution',selected=selected,**base,**val))
                        val=account_window(paths[mult],index[d['date']],h)
                        if d['action']=='DATA_BLOCKED':val=unknown('unknown_signal_availability')
                        outcomes.append(dict(category='technical_capital',selected=True,**base,**val))
            trades+=local;ledgers.append(dict(code=code,arm=arm,orders=ledger,transitions=s['transitions']))
    return outcomes,decisions,trades,ledgers,accounts


def build(out):
    verify_inputs(out);cfg=read(out/'frozen-contract.json');ps=panels(out,cfg)
    a,fd=fundamental(ps,read(out/'financial-data.json'),cfg)
    b,td,trades,ledgers,accounts=technical(ps,read(out/'warmup-data.json'),cfg);rows=a+b
    limitations=['当前存续预选样本，不是历史全市场成分或当前9持仓收益。',
                  '财报为本次厂商修订版本，仅公告日期过滤，不是原始公告版本PIT；ROE为原screen披露口径，未改为TTM。',
                  '历史名称/ST缺口逐月留标记；当前上市元数据不证明无退市幸存者偏差。',
                  '目标2021—2025；不足120日预热的交易日保留unknown；缺口后原状态无法连续则保留unknown。',
                  '2025末无2026延伸价格，20/40/60未成熟不删；已知子集均值不代表全分母。',
                  '港股沿用三源价格核对和公告重建资格，非逐笔/队列/成交量保证。',
                  '复权价收益不是现金红利全收益；零息现金、相同名义资本槽，事件重叠不可当组合年化。',
                  '日级估计复用MT13 ensure/renew/expire/transition恢复函数，同SELL信号分次尝试；缺入场指标仍核对冻结风险线退出。',
                  'technical_capital为每股每臂同初始10000资本逐日复投账本；窗口从下一收盘账户净值起量度，因此不是从零建仓的事件回报。已占用槽不再伪记现金。',
                  '2024—2025仅固定后段描述性检查，非严格从未见过的OOS。']
    incomplete=[]
    if not any(r['selected'] for r in fd):incomplete.append('基本面未产生入选：查看逐期缺口/过滤原因与补采失败，不等于0%收益。')
    if not read(out/'warmup-data.json').get('dates'):incomplete.append('2020预热未补齐，2021早段不可测。')
    return dict(status='partial' if incomplete else 'exploratory_complete_pending_independent_review',
                summary=summarize(rows),limitations=limitations,incomplete=incomplete,
                counts=dict(outcomes=len(rows),fundamental_decisions=len(fd),technical_decisions=len(td),execution_episodes=len(trades)),
                input_manifest_sha256=sha(out/'input-manifest.json')),dict(outcomes=rows,fundamental_decisions=fd,technical_decisions=td,trades=trades,ledgers=ledgers,capital_accounts=accounts)


def encode(value):return (json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False)+'\n').encode()


def history_code_hashes():
    """Independent history fingerprint; also seal all original direct/transitive modules."""
    root = Path(__file__).resolve().parents[2]
    paths = list((root/'mt1').glob('*.py')) + list(Path(__file__).parent.glob('*.py'))
    return {str(p.relative_to(root)):sha(p) for p in sorted(paths)}


def run(out):
    result,detail=build(out)
    seal_bytes(out/'samples.json.gz',gzip.compress(encode(detail),mtime=0));write(out/'results.json',result)
    seal_bytes(out/'RESULTS.md',render(result).encode())
    code=history_code_hashes()
    write(out/'run-receipt.json',dict(code_hashes=code,core_hashes={n:sha(out/n) for n in ('samples.json.gz','results.json','RESULTS.md')}))
    return result['counts']


def verify(out):
    receipt=read(out/'run-receipt.json');root=Path(__file__).resolve().parents[2]
    if receipt['code_hashes'] != history_code_hashes():raise ValueError('history_code_changed')
    for p,h in receipt['code_hashes'].items():
        if sha(root/p)!=h:raise ValueError('code_changed:'+p)
    result,detail=build(out)
    if read(out/'results.json')!=result or read(out/'samples.json.gz')!=detail:raise ValueError('offline_rebuild_mismatch')
    for n,h in receipt['core_hashes'].items():
        if sha(out/n)!=h:raise ValueError('output_hash_changed')
    result=dict(offline_rebuild_equal=True,core_hashes=receipt['core_hashes'],input_manifest_sha256=sha(out/'input-manifest.json'))
    write(out/'verify-receipt.json',result);return result


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('command',choices=['collect','run','verify'])
    ap.add_argument('--out',type=Path,required=True);ap.add_argument('--offline',action='store_true');a=ap.parse_args()
    result=collect(a.out,a.offline) if a.command=='collect' else run(a.out) if a.command=='run' else verify(a.out)
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
