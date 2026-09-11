"""Frozen, attributable A/B diagnostic replay. No parameter search, no trades.

A: identical supplied entry episodes, three exits. B: same audited panel and
exit rule, three entry rules. Execution unknowns stay in denominator, not filled
at convenient later prices. Synthetic correctness != efficacy evidence.
"""
from collections import Counter, defaultdict
import hashlib
from pathlib import Path
from datetime import datetime, timezone
from .timing import contract, digest, normalize, step, relative_strength, instant, indicators
from .parallel import timing as old_timing


def next_fill(rows, signal_index, side, market, cfg, entry_index=None):
    for i in range(signal_index+1, len(rows)):
        b = rows[i]
        if entry_index is not None and i-entry_index < cfg['execution']['min_holding_sessions'][market]:
            continue
        e = b.get('execution', {})
        if not all(k in e for k in ('buy','sell','settlement_ok','known_at','source')) or not e['source']:
            return {'status':'unknown', 'reason':'missing_suspension_limit_delisting_settlement_evidence', 'date':b['date']}
        try:
            if instant(e['known_at']) > instant(b['open_at']):
                return {'status':'unknown','reason':'execution_evidence_after_open'}
        except (KeyError, ValueError):
            return {'status':'unknown','reason':'execution_timestamp_missing'}
        if not all(isinstance(e[k],bool) for k in ('buy','sell','settlement_ok')):
            return {'status':'unknown','reason':'invalid_execution_flags'}
        if not e[side] or not e['settlement_ok']:
            continue  # verified unexecutable open; next exchange session, no stop-price fill.
        return {'status':'filled', 'index':i, 'date':b['date'], 'price':b['open'], 'source':e['source']}
    return {'status':'pending', 'reason':'no_later_verified_executable_open'}


def exit_signal(card, rule):
    if rule == 'old_ma60_5':
        return card['indicators']['below_ma60_5sessions']
    reasons = card['risk']['reasons']
    return any(r != 'ATR_trailing_close_breach' for r in reasons) if rule=='structure_failure' else bool(reasons)


def trade(panel, rows, signal_index, rule, cfg, breakout=None):
    result = {'signal_date':rows[signal_index]['date'], 'exit_rule':rule, 'net_price_return':None, 'max_drawdown':None,
              'post_exit_rally':None, 'false_breakout':None, 'round_trips':0, 'turnover':0}
    entry = next_fill(rows, signal_index, 'buy', panel['market'], cfg)
    result['entry_fill'] = entry
    if entry['status'] != 'filled':
        return {**result, 'status':entry['status']}
    start = entry['index']
    state = None
    exit_at = None
    # Exit monitoring begins from the SAME frozen signal structure, not different
    # intraday entry highs. Closing signal remains known before the entry open.
    state, _ = step(rows[:signal_index+1], None, cfg, channel=panel.get('channel','TREND'), observed_at=rows[signal_index]['close_at'])
    if breakout:
        state['breakout_episode'] = {**breakout, 'session':state['observed_sessions'], 'failed_closes':0}
    # Confirmed unavailable sessions between signal and entry still update state.
    for i in range(signal_index+1, len(rows)):
        state, card = step(rows[:i+1], state, cfg, channel=panel.get('channel','TREND'), observed_at=rows[i]['close_at'])
        if i < start:
            if exit_signal(card, rule):
                return {**result, 'status':'cancelled_before_entry', 'entry_fill':{'status':'cancelled','reason':'risk_before_delayed_entry'}}
            continue
        if exit_signal(card, rule) or i-start >= 60:
            exit_at = next_fill(rows, i, 'sell', panel['market'], cfg, start)
            result.update(exit_signal_date=rows[i]['date'], exit_reason=card['risk']['reasons'] if rule!='old_ma60_5' else ['old_ma60_or_horizon'], exit_fill=exit_at)
            break
    cost = cfg['execution']['per_side_cost_bps'][panel['market']]/10000
    result['turnover'] = 1
    if not exit_at or exit_at['status'] != 'filled':
        return {**result, 'status':exit_at['status'] if exit_at else 'not_matured'}
    end = exit_at['index']
    price = entry['price']
    net = exit_at['price']*(1-cost)/(price*(1+cost))-1
    # Close-based drawdown: no impossible ordering of daily high and low.
    wealth = [1] + [r['close']/(price*(1+cost)) for r in rows[start:end]] + [1+net]
    peak, dd = 1, 0
    for x in wealth:
        peak=max(peak,x); dd=min(dd,x/peak-1)
    rally = max(r['close'] for r in rows[end+1:end+21])/exit_at['price']-1 if len(rows)>end+20 else None
    failed = None
    if breakout and len(rows)>signal_index+cfg['failure_window_sessions']:
        count=0; failed=False
        for r in rows[signal_index+1:signal_index+1+cfg['failure_window_sessions']]:
            count=count+1 if r['close']<breakout['level'] else 0
            failed |= count>=cfg['failure_closes']
    return {**result, 'status':'closed', 'net_price_return':net, 'max_drawdown':dd,
            'round_trips':1, 'turnover':2, 'post_exit_rally':rally, 'false_breakout':failed}


def aggregate(trades):
    values = sorted(t['net_price_return'] for t in trades if t['net_price_return'] is not None)
    return {'sample_count':len(trades), 'status_counts':dict(Counter(t['status'] for t in trades)),
            'mean_net_price_return':sum(values)/len(values) if values else None,
            'worst_trade_tail_loss':min(values) if values else None,
            'worst_close_drawdown':min((t['max_drawdown'] for t in trades if t['max_drawdown'] is not None), default=None),
            'round_trips':sum(t['round_trips'] for t in trades), 'turnover_one_way_units':sum(t['turnover'] for t in trades),
            'false_breakouts':sum(t['false_breakout'] is True for t in trades),
            'false_breakout_unknown':sum(t['false_breakout'] is None for t in trades),
            'post_exit_rally_mean':sum(v)/len(v) if (v:=[t['post_exit_rally'] for t in trades if t['post_exit_rally'] is not None]) else None,
            'post_exit_rally_unknown':sum(t['post_exit_rally'] is None for t in trades)}


def compare(bundle, cfg=None):
    cfg = cfg or contract()
    out = {'contract_hash':digest(cfg), 'trial':cfg['trial'], 'mode':'diagnostic_current_vintage_NOT_PIT_backtest',
           'executed_at':datetime.now(timezone.utc).isoformat(),
           'implementation_hashes':{n:hashlib.sha256(Path(__file__).with_name(n).read_bytes()).hexdigest() for n in ('timing.py','timing_experiment.py')},
           'return_basis':'equal_weight_event_price_returns_not_portfolio_or_personal_PnL',
           'source_kind':bundle.get('source_kind','unknown'), 'scope_epoch':bundle['scope_epoch'],
           'audit':[], 'trades':[], 'folds':[], 'limitations':['no_survivorship_free_PIT_universe', 'fees_are_scenarios', 'no_automatic_promotion']}
    panels = {p['code']:p for p in bundle['panels']}
    if len(panels)!=len(bundle['panels']) or set(panels)-set(bundle['scope_codes']):
        raise ValueError('scope_or_duplicate_panel_violation')
    if any(e['code'] not in panels for e in bundle.get('entry_episodes',[])):
        raise ValueError('foreign_entry_episode')
    for code, panel in panels.items():
        try:
            rows = normalize(panel, bundle['asof'])
            if len(rows)<cfg['warmup']: raise ValueError('warmup_missing')
        except (ValueError, KeyError, TypeError) as e:
            out['audit'].append({'code':code,'status':'blocked','reason':str(e)})
            continue
        dates = {r['date']:i for i,r in enumerate(rows)}
        frozen = [e for e in bundle.get('entry_episodes',[]) if e['code']==code]
        seen = set()
        positions=sorted(dates[e['signal_date']] for e in frozen)
        if any(b-a<=60 for a,b in zip(positions,positions[1:])):
            raise ValueError('overlapping_A_episodes_require_60_session_purge')
        for e in frozen:
            if not e.get('id') or e['id'] in seen or not e.get('source_hash'):
                raise ValueError('unauditable_or_duplicate_entry')
            seen.add(e['id'])
            i = dates[e['signal_date']]
            if i < cfg['warmup']-1: raise ValueError('entry_warmup_missing')
            f=indicators(rows[:i+1])
            for rule in cfg['group_A']['exits']:
                t = trade(panel, rows, i, rule, cfg, e.get('breakout_episode'))
                out['trades'].append({**t, 'group':'A', 'arm':rule, 'code':code, 'episode_id':e['id'], 'entry_source_hash':e['source_hash'], 'market':panel['market'], 'industry':panel.get('industry','unknown'), 'volatility':'high' if f['atr20']/f['close']>.04 else 'low' if f['atr20']/f['close']<.02 else 'medium', 'regime':'uptrend' if f['close']>f['ma60']>f['ma60_5ago'] else 'other'})
        # Walk forward: train 120, embargo/purge 60, then independent 60-session
        # test blocks. No fitting. Same fold boundary shared by every entry arm.
        fold_start = cfg['evaluation']['oos_train_sessions']+cfg['evaluation']['purge_sessions']
        state = None
        last_entry = {}
        opportunities = Counter()
        for i in range(cfg['warmup']-1, len(rows)):
            state, card = step(rows[:i+1], state, cfg, channel=panel.get('channel','TREND'), observed_at=rows[i]['close_at'])
            if i < fold_start: continue
            # common purged blocks; follow-up return cannot cross test block end
            fold = (i-fold_start)//(cfg['evaluation']['oos_test_sessions']+cfg['evaluation']['purge_sessions'])
            start = fold_start+fold*(cfg['evaluation']['oos_test_sessions']+cfg['evaluation']['purge_sessions'])
            end = start+cfg['evaluation']['oos_test_sessions']
            if i>=end: continue
            if not any(f['code']==code and f['fold']==fold for f in out['folds']):
                out['folds'].append({'code':code,'fold':fold,'train_start_index':start-180,'train_end_index':start-61,'test_start_index':start,'test_end_index':end-1,'purge':60,'refit':False})
            f = card['indicators']
            broad=relative_strength(panel,rows[:i+1],'broad',bundle['asof'],cfg)
            old=old_timing({**f,'rs60':broad['value']},f['asof'],f['asof'])['status'] if broad['status']=='ok' else 'unknown'
            signals={'old_trigger':old, **{k:v['status'] for k,v in card['entry'].items()}}
            for arm, status in signals.items():
                opportunities[arm+'|'+status]+=1
                if status!='trigger' or i-last_entry.get(arm,-1000)<=60: continue
                last_entry[arm]=i
                breakout=state.get('breakout_episode') if arm=='breakout' else None
                t=trade(panel,rows[:min(end,len(rows))],i,cfg['group_B']['exit'],cfg,breakout)
                out['trades'].append({**t,'group':'B','arm':arm,'code':code,'fold':fold,'market':panel['market'],
                                      'industry':panel.get('industry','unknown'), 'volatility':'high' if f['atr20']/f['close']>.04 else 'low' if f['atr20']/f['close']<.02 else 'medium',
                                      'regime':'uptrend' if f['close']>f['ma60']>f['ma60_5ago'] else 'other'})
        out['audit'].append({'code':code,'status':'observed' if len(rows)>fold_start else 'not_matured', 'sessions':len(rows),'entry_opportunities':dict(opportunities), 'A_episodes':len(frozen)})
    out['summary']={g:{arm:aggregate([t for t in out['trades'] if t['group']==g and t['arm']==arm]) for arm in (cfg['group_A']['exits'] if g=='A' else cfg['group_B']['entries'])} for g in ('A','B')}
    strata=defaultdict(list)
    for t in out['trades']:
        for field in cfg['evaluation']['strata']:
            strata[t['group']+'|'+t['arm']+'|'+field+'|'+t[field]].append(t)
    out['strata']={k:aggregate(v) for k,v in strata.items()}
    out['status']='diagnostic_only_not_validated'
    return out
