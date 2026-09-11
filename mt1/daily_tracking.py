"""Read-only holdings/current-scope quotes, independent of legacy recommendation P&L."""
from datetime import datetime, timezone
import math
from .scope import load, counts
from .calendar import gate
from .data import cn_calendar, foreign_calendar


def market_of(code):
    return 'HK' if code.endswith('.HK') else 'CN' if code.endswith(('.SH', '.SZ', '.BJ')) else 'US'


def calendars(now):
    result = {}
    for market in ('CN', 'HK', 'US'):
        try:
            snapshot = cn_calendar(now) if market == 'CN' else foreign_calendar(now, market)
            result[market] = gate(snapshot, market, 'evening', now)
        except Exception as e:
            result[market] = {'market': market, 'allowed': False, 'expected_date': None,
                              'reason': 'calendar_failed', 'error': str(e)}
    return result


def dated_bars(bars, expected):
    if not expected:
        raise ValueError('calendar expected_date missing')
    def day(row):
        d = str(row.get('date') or row.get('trade_date') or '').replace('-', '')
        return d
    target = expected.replace('-', '')
    rows = sorted((r for r in bars if day(r) <= target), key=day)
    if not rows or day(rows[-1]) != target:
        raise ValueError('quote_stale_or_missing: expected ' + expected)
    if not math.isfinite(float(rows[-1]['close'])) or float(rows[-1]['close']) <= 0:
        raise ValueError('invalid close')
    return rows


def track(scope_path=None, now=None, fetch=None, gates=None):
    from thesis_enrich_daily import fetch_daily_bars
    scope = load(scope_path)
    if scope is None:
        raise ValueError('daily tracking requires explicit scope')
    now = now or datetime.now(timezone.utc)
    gates = calendars(now) if gates is None else gates
    fetch = fetch or fetch_daily_bars
    rows = []
    for code, item in {**scope['recommendations'], **scope['holdings']}.items():
        market = market_of(code)
        g = gates[market]
        row = {'code': code, 'name': item.get('name'), 'market': market,
               'holding_status': 'confirmed' if code in scope['holdings'] else 'not_held',
               'actual_cost': None, 'original_recommendation_date': None, 'original_reference_price': None,
               'recommendation_return': None, 'personal_pnl': None,
               'condition_status': 'not_verified_no_current_research_evidence',
               'quote_date': None, 'close': None, 'market_gate': g,
               'basis': 'provider_daily_close_adjustment_not_verified; fees_not_included; not_total_return',
               'source': 'thesis_enrich_daily.fetch_daily_bars', 'fetched_at': now.isoformat()}
        try:
            bars = dated_bars(fetch(code), g.get('expected_date'))
            row.update(status='ok', quote_date=g['expected_date'], close=float(bars[-1]['close']),
                       pct_today=(float(bars[-1]['close'])/float(bars[-2]['close'])-1)*100 if len(bars)>1 and float(bars[-2]['close'])>0 else None)
        except Exception as e:
            row.update(status='failed', error=str(e))
        rows.append(row)
    errors = [r for r in rows if r['status'] == 'failed']
    return {'asof': now.isoformat(), 'tracking_scope': counts(scope), 'markets': gates,
            'status': 'partial' if errors else 'ok', 'rows': rows,
            'failed': errors, 'historical_performance_included': False, 'read_only': True}
