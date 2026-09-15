"""Forward price observations from immutable daily freezes + exchange calendars.
No interpolation across absent snapshots. Financial causality still needs review.
"""
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from .ta_research import read, evidence, save, digest, evidence_errors
from .timing import instant
from .timing_cli import file_hash


def panel(inputs,stock,predicted_at,asof,root):
    tz=ZoneInfo('Asia/Hong_Kong' if stock['market']=='HK' else 'Asia/Shanghai')
    start=instant(predicted_at).astimezone(tz).date();end=instant(asof).astimezone(tz).date()
    dates={};sources=[];quotes={}
    for ptr,frozen in inputs:
        if instant(frozen['as_of'])>instant(asof):continue
        s=next((s for s in frozen['stocks'] if s['code']==stock['code']),None)
        if not s:continue
        q=next((e for e in s['evidence'] if e['kind']=='quote'),None)
        if not q:continue
        cp=Path(q['raw_path']).parent/('inputs/calendar_HK.json' if stock['market']=='HK' else 'inputs/calendar.json')
        cal=read(cp)
        sources.append({'path':str(cp),'sha256':file_hash(cp),'input_asof':frozen['as_of']})
        for d in cal['days']:
            if d['date'] in dates and dates[d['date']]!=d:raise ValueError('conflicting_exchange_calendar')
            dates[d['date']]=d
        if evidence_errors(q,asof):raise ValueError('invalid_forward_quote')
        if instant(q['published_at'])<=instant(predicted_at):continue
        date=instant(q['published_at']).astimezone(tz).date().isoformat()
        if date not in quotes or instant(q['fetched_at'])>instant(quotes[date]['fetched_at']):quotes[date]=q
    missing=[];d=start
    while d<=end:
        if d.isoformat() not in dates:missing.append(d.isoformat())
        d+=timedelta(days=1)
    if missing:return {'status':'blocked_calendar_gap','missing_dates':missing,'sessions':[],'observations':{},'evidence':[],'calendar':None}
    days=sorted((d for d in dates.values() if start.isoformat()<=d['date']<=end.isoformat()),key=lambda d:d['date'])
    sessions=sorted(d['close_at'] for d in days if d['is_open'] and instant(predicted_at)<instant(d['close_at'])<=instant(asof))
    calbody={'days':days,'sources':sources,'exchange':'HKEX' if stock['market']=='HK' else 'SSE'}
    folder=Path(root)/'forward-materials';cp=folder/(digest(calbody)+'.calendar.json');save(cp,calbody)
    cal=evidence(stock['code'],'calendar',calbody,'https://api.tushare.pro',asof,asof,cp)
    rows=[];quote_sources=[]
    for day in days:
        q=quotes.get(day['date'])
        if not q or not day['is_open'] or instant(q['published_at'])<instant(day['close_at']):continue
        rows.append({'code':stock['code'],'currency':q['currency'],'close_at':day['close_at'],'close':q['content']['last']})
        quote_sources.append({'evidence_id':q['evidence_id'],'raw_path':q['raw_path'],'raw_sha256':q['raw_sha256'],'provider_at':q['published_at'],'basis':'post_close_vendor_snapshot'})
    content={'code':stock['code'],'currency':'HKD' if stock['market']=='HK' else 'CNY','basis':'unadjusted_close_price_only','rows':rows,'sources':quote_sources}
    pp=folder/(digest(content)+'.prices.json');save(pp,content)
    ev=evidence(stock['code'],'price_panel',content,'https://qt.gtimg.cn/',asof,asof,pp)
    have={r['close_at'] for r in rows}
    observations={str(n):{'price_panel_id':ev['evidence_id']} for n in (20,40,60) if len(sessions)>n and sessions[0] in have and sessions[n] in have}
    return {'status':'verified_calendar','sessions':sessions,'observations':observations,'evidence':[ev] if rows else [],'calendar':cal,'missing_quote_sessions':[s for s in sessions if s not in have]}
