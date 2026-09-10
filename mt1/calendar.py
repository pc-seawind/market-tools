"""Explicit exchange sessions; no weekday fallback, including foreign markets."""
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

MARKETS = {'CN': ('Asia/Shanghai', 'SSE'), 'HK': ('Asia/Hong_Kong', 'HKEX'),
           'US': ('America/New_York', 'NYSE')}


def gate(snapshot, market, phase, now):
    if now.tzinfo is None:
        raise ValueError('now must include timezone')
    result = {'market': market, 'allowed': False, 'expected_date': None}
    if phase in ('saturday', 'sunday'):
        return {**result, 'allowed': True, 'reason': 'method_research_only'}
    try:
        tz, exchange = MARKETS[market]
        local = now.astimezone(ZoneInfo(tz))
        today = local.date()
        assert snapshot['exchange'] == exchange and snapshot['source']
        fetched = datetime.fromisoformat(snapshot['fetched_at'])
        assert fetched.tzinfo and timedelta(0) <= now - fetched <= timedelta(days=7)
        rows = {date.fromisoformat(r['date']): r for r in snapshot['days']}
        assert len(rows) == len(snapshot['days'])
        # Require complete recent calendar, including explicit holidays.
        for n in range(32):
            r = rows[today - timedelta(days=n)]
            assert type(r['is_open']) is bool
            if r['is_open']:
                close = datetime.fromisoformat(r['close_at'])
                assert close.tzinfo and close.astimezone(ZoneInfo(tz)).date() == today - timedelta(days=n)
        completed = [d for d, r in rows.items() if r['is_open'] and
                     datetime.fromisoformat(r['close_at']) <= now and
                     (phase != 'morning' or market != 'CN' or d < today)]
        assert completed
        expected = max(completed).isoformat()
        if not rows[today]['is_open']:
            return {**result, 'expected_date': expected, 'reason': 'market_closed'}
        if phase == 'evening' and market == 'CN' and expected != today.isoformat():
            return {**result, 'expected_date': expected, 'reason': 'session_not_completed'}
        return {**result, 'allowed': True, 'expected_date': expected, 'reason': 'verified'}
    except (KeyError, ValueError, AssertionError, TypeError):
        return {**result, 'reason': 'calendar_missing_invalid_or_stale'}


def check_fresh(actual, expected):
    return bool(expected and actual == expected)
