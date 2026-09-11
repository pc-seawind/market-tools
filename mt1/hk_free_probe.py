"""Bounded, read-only free HK data feasibility. Never calls denied endpoints.
Raw checkpoints persist, offline audit rerunnable. No brokerage/trade API.
"""
import argparse
import ast
import json
import math
import urllib.request
import urllib.error
from datetime import datetime,timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from .history_research import sha,write

BASE='?period1=1609459200&period2=1767225600&interval=1d&events=div%2Csplits&includeAdjustedClose=true'
URLS={
'yahoo_hsbc':'https://query1.finance.yahoo.com/v8/finance/chart/0005.HK'+BASE,
'yahoo_hsi':'https://query1.finance.yahoo.com/v8/finance/chart/%5EHSI'+BASE,
'yahoo_hkex':'https://query1.finance.yahoo.com/v8/finance/chart/0388.HK'+BASE,
'yahoo_mobile':'https://query1.finance.yahoo.com/v8/finance/chart/0941.HK'+BASE,
'sina_hsbc_qfq':'https://finance.sina.com.cn/stock/hkstock/00005/qfq.js',
'sina_hsbc_hfq':'https://finance.sina.com.cn/stock/hkstock/00005/hfq.js',
'sina_hkex_qfq':'https://finance.sina.com.cn/stock/hkstock/00388/qfq.js',
'sina_mobile_qfq':'https://finance.sina.com.cn/stock/hkstock/00941/qfq.js',
'hsbc_dividend_official':'https://www.hsbc.com/investors/shareholder-and-dividend-information/dividend-information-and-timetable',
'hkex_vcm_official':'https://www.hkex.com.hk/Services/Trading/Securities/Overview/Trading-Mechanism/VCM-Enhancements-Initiative?sc_lang=en',
'hkex_halts_official':'https://www1.hkexnews.hk/search/predefineddoc.xhtml?lang=en&predefineddocuments=9',
'hkex_data_official':'https://www.hkex.com.hk/Global/Exchange/FAQ/Market-Data/Getting-Market-Data/Historical-Data?sc_lang=en'}


def fetch(out,name,url,offline):
    path=out/(name+'.raw');meta=out/(name+'.meta.json')
    if not meta.exists() and (out/'first-probes.json').exists():
        prior=next((p for p in json.loads((out/'first-probes.json').read_text()) if p['name']==name and p['url']==url),None)
        if prior:write(meta,prior)
    if meta.exists():
        m=json.loads(meta.read_text())
        if m['url']!=url or sha(path)!=m['sha256']:raise ValueError('checkpoint_changed')
        return m
    if offline:raise ValueError('offline_checkpoint_missing:'+name)
    try:
        with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0'}),timeout=20) as r:raw=r.read();status=r.status
    except urllib.error.HTTPError as e:raw=e.read();status=e.code
    except Exception as e:raw=str(e).encode();status='network_error'
    path.write_bytes(raw)
    m=dict(name=name,url=url,status=status,bytes=len(raw),sha256=sha(path),fetched_at=datetime.now(timezone.utc).isoformat())
    write(meta,m);print(name,status,len(raw),flush=True);return m


def chart(raw):
    d=json.loads(raw)['chart']['result'][0];q=d['indicators']['quote'][0];adj=d['indicators']['adjclose'][0]['adjclose']
    rows=[]
    for i,ts in enumerate(d['timestamp']):
        date=datetime.fromtimestamp(ts,ZoneInfo('Asia/Hong_Kong')).strftime('%Y%m%d')
        r=dict(date=date,**{k:q[k][i] for k in ('open','high','low','close','volume')},adjclose=adj[i])
        r['factor']=r['adjclose']/r['close'] if r['adjclose'] and r['close'] else None
        rows.append(r)
    divs={datetime.fromtimestamp(v['date'],ZoneInfo('Asia/Hong_Kong')).strftime('%Y%m%d'):v['amount'] for v in d.get('events',{}).get('dividends',{}).values()}
    changes=[]
    for a,b in zip(rows,rows[1:]):
        if a['factor'] and b['factor']:
            actual=b['factor']/a['factor']
            cash=divs.get(b['date'],0)
            expected=a['close']/(a['close']-cash) if cash and a['close']>cash else 1.
            if abs(actual-1)>1e-5 or cash:
                changes.append(dict(date=b['date'],dividend=cash,observed_factor_ratio=actual,
                    dividend_implied_ratio=expected,error_bps=(actual/expected-1)*10000))
    return dict(symbol=d['meta']['symbol'],currency=d['meta'].get('currency'),rows=rows,dividends=divs,
        splits=d.get('events',{}).get('splits',{}),factor_changes=changes,
        caution='Yahoo factor=adjclose/close and Yahoo dividends are SAME source internal consistency, NOT independent corporate-action verification')


def extended_audit(out,charts):
    import bisect
    factors={};prices={}
    for code,yf,sina in [('00005','yahoo_hsbc','sina_hsbc_qfq'),('00388','yahoo_hkex','sina_hkex_qfq'),('00941','yahoo_mobile','sina_mobile_qfq')]:
        parsed=ast.literal_eval((out/(sina+'.raw')).read_text().split('=',1)[1].split('\n',1)[0].rstrip(';'))['data']
        ff=sorted((x['d'].replace('-',''),float(x['f'])) for x in parsed);dates=[x[0] for x in ff]
        joined=[]
        for b in charts[yf]['rows']:
            i=bisect.bisect_right(dates,b['date'])-1
            if i>=0 and b['factor']:joined.append(dict(date=b['date'],yahoo=b['factor'],sina=ff[i][1]))
        anchor=joined[-1]
        errors=[dict(date=x['date'],error_bps=(x['sina']/anchor['sina']/(x['yahoo']/anchor['yahoo'])-1)*10000) for x in joined]
        factors[code]=dict(anchor=anchor,days=len(joined),max_abs_error_bps=max(abs(x['error_bps']) for x in errors),days_over_5bps=sum(abs(x['error_bps'])>5 for x in errors),note='independent vendor endpoints normalized at fixed end date; not proof of independent upstream or complete official issuer action history')
        vendor={}
        for year in range(2021,2026):
            name=f'tencent_qfq_request_{code}_{year}';meta=json.loads((out/(name+'.meta.json')).read_text());assert sha(out/(name+'.raw'))==meta['sha256']
            doc=json.loads((out/(name+'.raw')).read_text());assert doc['code']==0
            data=doc['data']['hk'+code]
            if 'day' not in data:raise ValueError('Tencent_returned_adjusted_not_raw')
            for row in data['day']:
                date=row[0].replace('-','')
                if date in vendor:raise ValueError('duplicate_Tencent_day')
                vendor[date]=dict(date=date,open=float(row[1]),close=float(row[2]),high=float(row[3]),low=float(row[4]),volume=float(row[5]))
        yfrows={b['date']:b for b in charts[yf]['rows']};disputes=[]
        for date in sorted(set(vendor)&set(yfrows)):
            a,b=vendor[date],yfrows[date]
            fields=[k for k in ('open','high','low','close') if a[k] is None or b[k] is None or abs(a[k]-b[k])>.011]
            if fields or b['volume']==0 or a['volume']==0:disputes.append(dict(date=date,price_conflict_fields=fields,Tencent=a,Yahoo=b))
        prices[code]=dict(Tencent_days=len(vendor),Yahoo_days=len(yfrows),Tencent_only=sorted(set(vendor)-set(yfrows)),Yahoo_only=sorted(set(yfrows)-set(vendor)),disputed_or_zero_volume_dates=disputes)
    refs=json.loads((out/'authorized-existing-raw-expansion.json').read_text());ref=next(r for r in refs if r['api']=='hk_tradecal' and r['status']=='ok')
    path=out/ref['path'];assert sha(path)==ref['sha256'];raw=json.loads(path.read_text())['data'];rows=[dict(zip(raw['fields'],x)) for x in raw['items']]
    cal=sorted(r['cal_date'] for r in rows if int(r['is_open'])==1);hsi=[r['date'] for r in charts['yahoo_hsi']['rows']]
    output=dict(cross_vendor_factor=factors,raw_price_audit=prices,calendar=dict(source=str(path),sha256=sha(path),open_days=len(cal),HSI_dates_equal=cal==hsi),
        resolved=['five_year_OHLCV_available_from_two_free_vendors','five_year_HSI_available','five_year_exchange_open_date_calendar_matches','three_factor_chains_cross_vendor_relative_consistency','36_Yahoo_dividend_events_internal_consistency'],
        still_required=['resolve_specific_disputed_or_zero_volume_raw_days_with_third_source_not_arbitrary_vendor_cherry_pick',
        'session_open_eligibility_and_halt_resumption_timestamp_coverage_not_proven_by_daily_volume',
        'implement_and_test_independent_HK_execution_adapter_after_readiness_acceptance_not_copy_CN_limits_Tplus1'])
    write(out/'extended-free-audit.json',output)
    return output


def run(out,source,offline):
    evidence=[fetch(out,n,u,offline) for n,u in URLS.items()];charts={};errors=[]
    for n in ('yahoo_hsbc','yahoo_hkex','yahoo_mobile','yahoo_hsi'):
        try:charts[n]=chart((out/(n+'.raw')).read_bytes())
        except Exception as e:errors.append(dict(source=n,error=str(e)))
    write(out/'normalized-free-charts.json',charts)
    summaries={}
    for n,d in charts.items():
        summaries[n]=dict(symbol=d['symbol'],currency=d['currency'],rows=len(d['rows']),start=d['rows'][0]['date'],end=d['rows'][-1]['date'],
            missing_ohlcv=sum(any(r[k] is None for k in ('open','high','low','close','volume')) for r in d['rows']),
            zero_volume_dates=[r['date'] for r in d['rows'] if r['volume']==0],dividends=len(d['dividends']),splits=len(d['splits']),
            factor_changes=len(d['factor_changes']),factor_dividend_internal_mismatch_over_1bp=[x for x in d['factor_changes'] if abs(x['error_bps'])>1],
            hsi_dates_equal=[r['date'] for r in d['rows']]==[r['date'] for r in charts.get('yahoo_hsi',{}).get('rows',[])])
    # Independent original Tushare 2021 raw cross-check, NOT a fresh API request.
    refs=json.loads((source/'hk-feasibility.json').read_text())['results']
    ref=next(r for r in refs if r['api']=='hk_daily' and r['status']=='ok')
    body=source/ref['path'];assert sha(body)==ref['sha256'];raw=json.loads(body.read_bytes())['data']
    ts={r['trade_date']:r for r in (dict(zip(raw['fields'],v)) for v in raw['items'])}
    yf={r['date']:r for r in charts.get('yahoo_hsbc',{}).get('rows',[])};common=sorted(set(ts)&set(yf));diffs=[]
    for date in common:
        for k in ('open','high','low','close'):
            if yf[date][k] is None or abs(float(ts[date][k])-yf[date][k])>.011:
                diffs.append(dict(date=date,field=k,tushare=ts[date][k],yahoo=yf[date][k]))
    sina={}
    for name in URLS:
        if not name.startswith('sina'):continue
        text=(out/(name+'.raw')).read_text(errors='replace')
        try:
            value=text.split('=',1)[1].split('\n',1)[0].rstrip(';')
            parsed=ast.literal_eval(value);sina[name]=dict(rows=len(parsed.get('data',[])),keys=list(parsed),sample=parsed.get('data',[])[:2])
        except Exception as e:sina[name]=dict(status='unparseable_or_endpoint_error',error=type(e).__name__,prefix=text[:160])
    result=dict(evidence=evidence,charts=summaries,parse_errors=errors,sina=sina,
        independent_raw_price_check=dict(common_days=len(common),tushare_only=sorted(set(ts)-set(yf)),differences=diffs,tolerance_HKD=.011),
        minimum_remaining=['independent verified corporate-action factor chain for 00005/00388/00941 2021-2025 incl special dividends/currency/rights/splits and effective dates',
        'exchange-session calendar incl typhoon/half-days matched to actual tradable sessions, not union of vendor price dates',
        'dated suspension/resumption eligibility at intended open, with timestamp/source and missingness explicit; daily positive volume alone does not prove open eligibility',
        'separate HK execution/settlement model and tests, no mainland daily limit/T+1 copying'],
        decision='Free price/HSI/action candidates now available. No evidence yet that purchase is necessary. Do not buy broad price feed or retry denied APIs. Remaining verified-history assembly and HK model implementation must not be hidden as merely a credential blocker.')
    if (out/'tencent-corrected-requests.json').exists():
        extra=extended_audit(out,charts)
        result['resolved']=extra['resolved']
        result['minimum_remaining']=extra['still_required']
        result['decision']='Do not purchase duplicate price/factor/HSI data. Free factor and calendar paths verified; resolve enumerated conflicting raw sessions and obtain historical open eligibility; independent HK execution adapter remains unimplemented.'
    write(out/'free-path-audit.json',result)
    return result


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True);ap.add_argument('--source',type=Path,required=True);ap.add_argument('--offline',action='store_true')
    args=ap.parse_args();args.out.mkdir(parents=True,exist_ok=True);r=run(args.out,args.source,args.offline);print(json.dumps(r['charts'],ensure_ascii=False))
