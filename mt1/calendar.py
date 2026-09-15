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
        if phase == 'research':
            return {**result, 'allowed': True, 'expected_date': expected, 'reason': 'last_completed_session_verified'}
        if not rows[today]['is_open']:
            return {**result, 'expected_date': expected, 'reason': 'market_closed'}
        if phase == 'evening' and market == 'CN' and expected != today.isoformat():
            return {**result, 'expected_date': expected, 'reason': 'session_not_completed'}
        return {**result, 'allowed': True, 'expected_date': expected, 'reason': 'verified'}
    except (KeyError, ValueError, AssertionError, TypeError):
        return {**result, 'reason': 'calendar_missing_invalid_or_stale'}


def check_fresh(actual, expected):
    return bool(expected and actual == expected)


def review_deadline(value, market='CN'):
    """Date-only review means end of that LOCAL date, not its start.
    Explicit timestamps remain exact. Invalid/naive timestamps fail closed.
    """
    from datetime import time
    if len(value) == 10:
        return datetime.combine(date.fromisoformat(value), time.max, ZoneInfo(MARKETS[market][0]))
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError('review timestamp must include timezone')
    return result


def research_snapshot(snapshot, next_calendar, market):
    """Bridge midnight using archived exchange rows, never weekday inference.
    CN has a fixed normal close. HK half-day schedules need an explicit close;
    unsupported future HK rows are deliberately not synthesized.
    """
    import copy
    out=copy.deepcopy(snapshot)
    if market!='CN':return out
    assert next_calendar.get('code')==0
    data=next_calendar['data'];known={r['date'] for r in out['days']}
    for values in data['items']:
        r=dict(zip(data['fields'],values))
        if r['exchange']!=MARKETS[market][1] or str(r['is_open']) not in ('0','1'):
            raise ValueError('calendar exchange/session mismatch')
        d=datetime.strptime(r['cal_date'],'%Y%m%d').date().isoformat()
        if d not in known:
            out['days'].append({'date':d,'is_open':str(r['is_open'])=='1','close_at':d+'T15:00:00+08:00'})
            known.add(d)
    return out


def next_research_session(next_calendar, expected_date):
    """No valid completed date means unknown target, never abort other stocks."""
    if not expected_date:return None
    data=next_calendar['data']
    rows=[dict(zip(data['fields'],r)) for r in data['items']]
    dates=sorted(datetime.strptime(r['cal_date'],'%Y%m%d').date().isoformat()
                 for r in rows if str(r['is_open'])=='1' and r['cal_date']>expected_date.replace('-',''))
    return dates[0] if dates else None
