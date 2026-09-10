"""Decision clock, separate from closing-price date. No implicit local timezone."""
from datetime import datetime, time, timezone, timedelta
from zoneinfo import ZoneInfo
from .candidates import day

CN=ZoneInfo('Asia/Shanghai')


def instant(value, *, date_bound=None):
    if isinstance(value,datetime): dt=value
    else:
        value=str(value)
        if len(value) in (8,10):
            if date_bound is None:raise ValueError('exact timezone-aware instant required')
            dt=datetime.combine(day(value),time.max if date_bound=='end' else time.min,CN)
        else:dt=datetime.fromisoformat(value.replace('Z','+00:00'))
    if dt.tzinfo is None or abs(dt.utcoffset())>timedelta(hours=14):raise ValueError('timezone missing/invalid')
    return dt.astimezone(timezone.utc)


def review_times(packet, price_asof, decision_at):
    decision=instant(decision_at)
    price_close=datetime.combine(day(price_asof),time(15),CN).astimezone(timezone.utc)
    if price_close>decision:raise ValueError('price future/unclosed at decision')
    # Legacy day-only review means recorded review day, not exact event time.
    # New writers always emit exact timestamps; replay with date-only same-day
    # sources is conservative (publication known only by end of that day).
    reviewed=instant(packet['reviewed_at'],date_bound='start')
    expiry=instant(packet['valid_until'],date_bound='end')
    if not reviewed<=decision<=expiry or not timedelta(0)<=expiry-reviewed<=timedelta(days=32):
        raise ValueError('future review/expired evidence')
    for s in packet['sources']:
        published=instant(s['published_at'],date_bound='end')
        if published>reviewed or published>decision:raise ValueError('future or not yet published at review')
    return {'price_asof':str(day(price_asof)),'decision_at':decision.isoformat(),
            'reviewed_at':reviewed.isoformat(),'valid_until':expiry.isoformat(),
            'review_precision':'date' if len(str(packet['reviewed_at'])) in (8,10) else 'instant'}
