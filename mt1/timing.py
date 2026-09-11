"""Causal MT-1.2 shadow timing. No discovery, ledger, watchlist or trading writes.

Prices internally raw * vendor adjustment factor (one constant price unit).
Only the *display* conversion divides by today's factor. Revisions to already
observed OHLC/factors block continuation instead of max'ing incompatible prices.
"""
import copy
import hashlib
import json
import math
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from statistics import mean
from .parallel import timing as old_timing

CONTRACT_PATH = Path(__file__).resolve().parents[1] / 'docs/mt12/experiment-contract.json'


def contract():
    return json.loads(CONTRACT_PATH.read_text())


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def instant(s):
    t = datetime.fromisoformat(s.replace('Z', '+00:00'))
    if t.tzinfo is None:
        raise ValueError('timezone_required')
    return t


def positive(x):
    x = float(x)
    if not math.isfinite(x) or x <= 0:
        raise ValueError('invalid_positive_number')
    return x


def normalize(panel, asof):
    """Fail closed on missing calendar, factors, unfinished/future/duplicate bars.

    A panel's fetched_at is a real acquisition timestamp, NOT a historic PIT
    availability stamp. Current-vintage replay is explicitly separate.
    """
    if panel.get('adjustment') != 'vendor_factor_verified' or not panel.get('basis_id'):
        raise ValueError('adjustment_unknown_risk_prices_blocked')
    if not panel.get('calendar_verified'):
        raise ValueError('calendar_unknown')
    cutoff = instant(asof)
    market_tz={'CN':'Asia/Shanghai','HK':'Asia/Hong_Kong','US':'America/New_York'}[panel['market']]
    if str(cutoff.astimezone(ZoneInfo(market_tz)).date()) > panel.get('calendar_coverage_through',''):
        raise ValueError('calendar_refresh_required_for_new_local_day')
    if instant(panel['fetched_at']) > cutoff:
        raise ValueError('source_not_yet_available')
    sessions = panel['sessions']
    if not sessions or sessions != sorted(set(sessions)):
        raise ValueError('calendar_order_or_duplicate')
    rows = panel['bars']
    if [b['date'] for b in rows] != sessions:
        raise ValueError('price_calendar_hole_duplicate_or_foreign')
    result = []
    for b in rows:
        if str(instant(b['close_at']).astimezone(ZoneInfo(market_tz)).date()) != b['date']:
            raise ValueError('bar_close_timestamp_date_mismatch')
        if instant(b['close_at']) > cutoff:
            raise ValueError('unfinished_or_future_bar')
        if b.get('code', panel['code']) != panel['code']:
            raise ValueError('foreign_bar')
        factor = positive(b.get('factor'))
        r = {**b, **{k: positive(b[k]) * factor for k in ('open','high','low','close')},
             'factor': factor, 'vol': positive(b['vol'])}
        if not r['low'] <= min(r['open'], r['close']) <= max(r['open'], r['close']) <= r['high']:
            raise ValueError('invalid_ohlc')
        result.append(r)
    if panel.get('expected_date') != sessions[-1]:
        raise ValueError('stale_or_wrong_market_session')
    return result


def relative_strength(panel, rows, kind, asof, cfg):
    b = panel.get('benchmarks', {}).get(kind)
    reason = 'benchmark_missing'
    try:
        assert b and b['market'] == panel['market']
        assert instant(b['fetched_at']) <= instant(asof)
        assert b['price_basis'] == 'compatible_price_return'
        if kind == 'broad':
            assert b['id'] == cfg['benchmarks'][panel['market']]
        else:
            assert b['membership_basis'] and instant(b['membership_known_at']) <= instant(asof)
        values = b['bars']
        assert len({r['date'] for r in values}) == len(values)
        lookup = {r['date']: positive(r['close']) for r in values}
        assert len(rows) >= 61 and rows[-1]['date'] in lookup and rows[-61]['date'] in lookup
        value = rows[-1]['close']/rows[-61]['close'] - lookup[rows[-1]['date']]/lookup[rows[-61]['date']]
        return {'status':'ok', 'value':value, 'benchmark':b['id'], 'basis':b.get('membership_basis','market_price_return'), 'RS_not_RSI':True}
    except (AssertionError, KeyError, TypeError, ValueError):
        return {'status':'unknown', 'value':None, 'reason':reason+'_or_incompatible_market_dates_membership', 'RS_not_RSI':True}


def indicators(rows):
    c = [r['close'] for r in rows]
    ma20, ma60 = mean(c[-20:]), mean(c[-60:])
    tr = [max(r['high']-r['low'], abs(r['high']-rows[i-1]['close']), abs(r['low']-rows[i-1]['close'])) for i,r in enumerate(rows) if i]
    atr = mean(tr[-20:])
    return {'asof':rows[-1]['date'].replace('-',''), 'close':c[-1], 'ma20':ma20, 'ma60':ma60,
            'ma60_5ago':mean(c[-65:-5]), 'extension20':c[-1]/ma20-1,
            'atr20':atr, 'extension_atr':(c[-1]-ma20)/atr if atr else None,
            'previous_high20':max(r['high'] for r in rows[-21:-1]),
            'volume_ratio':rows[-1]['vol']/mean(r['vol'] for r in rows[-21:-1]),
            'today_low':rows[-1]['low'],
            'below_ma60_5sessions':all(c[i]<mean(c[i-59:i+1]) for i in range(len(c)-5,len(c)))}


def step(rows, state, cfg, *, channel, observed_at):
    """One completed session; structures frozen BEFORE any later trigger.

    Confirmation is rolling low known at the prior close, not a future pivot.
    A setup expires by observed exchange sessions, not weekdays/calendar weeks.
    """
    s = copy.deepcopy(state or {})
    b, prev = rows[-1], rows[-2]
    f = indicators(rows)
    s.setdefault('entries', {})
    s.setdefault('observed_sessions', 0)
    s.setdefault('condition_history', [])
    s['observed_sessions'] += 1
    n = s['observed_sessions']
    events = []
    common = channel != 'TREND' or (b['close'] > f['ma60'] > f['ma60_5ago'])
    for path in ('breakout','pullback'):
        p = s['entries'].get(path, {'status':'watch_structure','reason':'await_completed_structure'})
        if p['status'] == 'setup':
            if b['close'] < p['risk']:
                p.update(status='cancel', reason='frozen_structure_invalidated', cancel_date=b['date'])
            elif n-p['formed_session'] > cfg['setup_ttl_sessions']:
                p.update(status='cancel', reason='setup_expired', cancel_date=b['date'])
            elif b['date'] > p['formed_date'] and common:
                breakout = b['close'] > p['level'] and f['volume_ratio'] >= cfg['breakout_volume_min']
                pullback = b['close'] > prev['high'] and b['close'] > f['ma20'] and b['close'] > b['open']
                if (breakout if path == 'breakout' else pullback):
                    p.update(status='trigger', reason='later_close_price_volume_confirmation' if path=='breakout' else 'later_close_reclaims_MA20_and_prior_high', trigger_date=b['date'], trigger_price=b['close'])
                    events.append({'kind':path+'_trigger', 'date':b['date'], 'price':b['close']})
                    if path=='breakout':
                        s['breakout_episode'] = {'date':b['date'], 'session':n, 'level':p['level'], 'risk':p['risk'], 'failed_closes':0}
        elif p['status'] in ('watch_structure','cancel','trigger'):
            # A trigger is valid for its completed session only; next session may
            # form a NEW setup. Previous episode remains separate for failure.
            p = {'status':'watch_structure', 'reason':'await_completed_structure'}
            structure = rows[-cfg['structure_window']:]
            hi, lo = max(r['high'] for r in structure), min(r['low'] for r in structure)
            widths = [r['high']-r['low'] for r in structure]
            compression = (hi/lo-1 <= cfg['compression_range_max'] and mean(widths[-5:]) <= cfg['compression_recent_to_prior']*mean(widths[:-5]))
            pull_zone = b['low'] <= f['ma20']*(1+cfg['pullback_ma20_band']) and b['close'] >= f['ma20'] and f['volume_ratio'] <= cfg['pullback_volume_max']
            if compression if path=='breakout' else pull_zone:
                p = {'status':'setup', 'reason':'range_and_range_contraction_frozen' if path=='breakout' else 'MA20_low_volume_observation_NOT_support_confirmation',
                     'formed_date':b['date'], 'formed_session':n, 'known_at':observed_at,
                     'risk':lo, 'level':hi if path=='breakout' else b['high'], 'structure_start':structure[0]['date'],
                     'expires_after_sessions':cfg['setup_ttl_sessions']}
        s['entries'][path] = p
    if 'monitor' not in s:
        prior = rows[-cfg['structure_window']-1:-1]
        s['monitor'] = {'origin_date':b['date'], 'observed_at':observed_at, 'reference_price':b['close'],
                        'reference_is_user_cost':False, 'highest':b['close'],
                        'structure_low':min(r['low'] for r in prior), 'structure_start':prior[0]['date'],
                        'structure_known_date':prior[-1]['date'], 'structure_basis':'prior_completed_20_session_low_not_pivot',
                        'atr_stop':None, 'risk_trigger':None}
        # First observation's earlier intraday high was NOT monitored.
    else:
        s['monitor']['highest'] = max(s['monitor']['highest'], b['high'])
    m = s['monitor']
    prior_stop = m['atr_stop']
    candidate = m['highest']-cfg['atr_multiple']*f['atr20']
    m['atr_stop'] = max(prior_stop, candidate) if prior_stop is not None else candidate if candidate>0 else None
    failure = False
    ep = s.get('breakout_episode')
    if ep and n > ep['session'] and n-ep['session'] <= cfg['failure_window_sessions']:
        ep['failed_closes'] = ep['failed_closes']+1 if b['close'] < ep['level'] else 0
        failure = ep['failed_closes'] >= cfg['failure_closes']
    risks = []
    if b['close'] < m['structure_low']: risks.append('initial_structure_invalidated')
    if failure: risks.append('registered_breakout_failed')
    if m['atr_stop'] is not None and b['close'] < m['atr_stop']: risks.append('ATR_trailing_close_breach')
    if risks and m['risk_trigger'] is None:
        m['risk_trigger'] = {'date':b['date'], 'known_at':observed_at, 'reasons':risks, 'signal_close':b['close'], 'execution':'pending_next_executable_session_not_SELL'}
        events.append({'kind':'risk_exit_trigger', **m['risk_trigger']})
    intrabar = prior_stop is not None and b['low'] < prior_stop <= b['high']
    s['condition_history'].append({'date':b['date'], 'observed_at':observed_at, 'entry':copy.deepcopy(s['entries']), 'risk_reasons':risks, 'events':events})
    s.update(last_date=b['date'], method=cfg['method'], channel=channel)
    return s, {'entry':copy.deepcopy(s['entries']), 'risk':{'status':'risk_exit_trigger' if m['risk_trigger'] else 'monitor', 'reasons':risks, 'original_trigger':m['risk_trigger'],
                 'intrabar_order':'unknown_no_intrabar_fill' if intrabar else 'not_used_close_only'}, 'indicators':f, 'events':events}



def freeze_horizons(state, rows, panel, asof, cfg):
    """Persist observed horizon prices; a rolling input must not erase outcomes.

    Session age comes from causally continued state, not calendar days. Missing
    target bars remain blocked; no nearest-price substitution or future fill.
    """
    saved = state.setdefault('horizon_observations', {})
    age = state['observed_sessions'] - 1
    for h in cfg['horizons_sessions']:
        key = str(h)
        if key in saved or age < h:
            continue
        target_index = len(rows) - 1 - (age - h)
        if target_index < 0:
            continue
        bar = rows[target_index]
        raw = next(b for b in panel['bars'] if b['date'] == bar['date'])
        saved[key] = {
            'sessions': h, 'status': 'observed_price_only',
            'reference_price_return': bar['close']/state['monitor']['reference_price']-1,
            'target_date': bar['date'], 'target_adjusted_close': bar['close'],
            'reference_adjusted_close': state['monitor']['reference_price'],
            'basis_id': panel['basis_id'], 'source_bar_sha256': digest(raw),
            'source_panel_sha256': digest(panel), 'observed_at': asof,
            'not_personal_pnl': True,
        }
    return [copy.deepcopy(saved[str(h)]) if str(h) in saved else {
        'sessions': h, 'status': 'not_matured' if age < h else 'blocked',
        'reference_price_return': None, 'not_personal_pnl': True,
    } for h in cfg['horizons_sessions']]


def evaluate(panel, *, asof, previous=None, channel='TREND', cfg=None):
    cfg = cfg or contract()
    out = {'code':panel['code'], 'market':panel['market'], 'method':cfg['method'], 'method_status':'shadow',
           'input_asof':asof, 'channel':channel, 'final_buy':False, 'formal_sell':False, 'personal_pnl':None,
           'actual_cost':None, 'actual_entry_date':None, 'next_review':'next_completed_market_session; original_plan_deadline_unchanged',
           'pit':'current_vendor_vintage_diagnostic_not_historical_PIT', 'gaps':[], 'state':previous}
    try:
        if channel not in ('TREND','VALUE','REVERSAL'): raise ValueError('unsupported_channel')
        rows = normalize(panel, asof)
        if len(rows) < cfg['warmup']: raise ValueError('insufficient_120_session_warmup')
        state = copy.deepcopy(previous)
        if state:
            if state['contract_hash'] != digest(cfg) or state['basis_id'] != panel['basis_id']:
                raise ValueError('contract_or_price_basis_changed_new_review_required')
            if state['channel'] != channel: raise ValueError('channel_changed_new_review_required')
            if state['code'] != panel['code']: raise ValueError('foreign_state')
            # All available overlap must agree; require last observation in panel.
            keyed = {b['date']:digest(b) for b in panel['bars']}
            if state['last_date'] not in keyed or any(keyed[d]!=h for d,h in state['bar_hashes'].items() if d in keyed):
                raise ValueError('observed_price_or_factor_revised_or_history_gap')
            indexes = [i for i,r in enumerate(rows) if r['date']>state['last_date']]
            if not indexes:
                card=copy.deepcopy(state['last_card'])
                out['idempotent']=True  # refresh auxiliary provenance without replaying a session
        else:
            indexes = [len(rows)-1]  # forward episode starts NOW, no historic user highs/cost.
        for i in indexes:
            state, card = step(rows[:i+1], state, cfg, channel=channel, observed_at=asof)
            freeze_horizons(state, rows[:i+1], panel, asof, cfg)
        broad = relative_strength(panel, rows, 'broad', asof, cfg)
        industry = relative_strength(panel, rows, 'industry', asof, cfg)
        f = card['indicators']
        legacy = old_timing({**f,'rs60':broad['value']}, f['asof'], f['asof']) if broad['status']=='ok' else {'status':'unknown','reasons':['market_benchmark_unavailable_no_CSI300_fallback']}
        card.update(old_entry=legacy, old_exit={'status':'exit_review' if f['below_ma60_5sessions'] else 'monitor', 'rule':'5_completed_closes_below_each_MA60'},
                    rs={'broad':broad,'industry':industry}, display_factor=rows[-1]['factor'], quote_date=rows[-1]['date'], close=rows[-1]['close']/rows[-1]['factor'],
                    structure_price=state['monitor']['structure_low']/rows[-1]['factor'], atr20=f['atr20']/rows[-1]['factor'],
                    atr_stop=state['monitor']['atr_stop']/rows[-1]['factor'] if state['monitor']['atr_stop'] is not None else None,
                    extension={'old_fixed_10pct':f['extension20']>.1, 'ATR_multiple':f['extension_atr'], 'exploratory_extended':f['extension_atr'] is not None and f['extension_atr']>cfg['extension_atr_diagnostic'], 'veto':False},
                    price_basis='internal OHLC/entry/state = raw * adjustment_factor; close/structure_price/atr20/atr_stop = current raw units',
                    entry_display={k:{'status':v['status'],'level':v.get('level')/rows[-1]['factor'] if v.get('level') is not None else None, 'risk':v.get('risk')/rows[-1]['factor'] if v.get('risk') is not None else None, 'trigger_price':v.get('trigger_price')/rows[-1]['factor'] if v.get('trigger_price') is not None else None} for k,v in card['entry'].items()},
                    explanation='新增入场资格与已有持仓风险独立；结构/ATR风险触发可独立复核，不等财报证伪；shadow不是BUY/SELL。')
        card['horizons'] = freeze_horizons(state, rows, panel, asof, cfg)
        state.update(code=panel['code'], basis_id=panel['basis_id'], contract_hash=digest(cfg), bar_hashes={b['date']:digest(b) for b in panel['bars']}, last_card=card)
        out.update(card, state=state, status='ok')
        out['gaps'] = (['ATR_stop_nonpositive_unusable'] if card['atr_stop'] is None else []) + [k+'_RS_unknown' for k,v in card['rs'].items() if v['status']=='unknown']
    except (ValueError, KeyError, TypeError, ZeroDivisionError) as e:
        out.update(status='blocked', gaps=[str(e)], entry={'status':'not_ready'}, risk={'status':'unknown'}, old_entry={'status':'unknown'}, old_exit={'status':'unknown'},
                   structure_price=None, atr20=None, atr_stop=None)
        out['gaps'] += [k+'_RS_unknown' for k in ('broad','industry') if not panel.get('benchmarks',{}).get(k)]
    return out
