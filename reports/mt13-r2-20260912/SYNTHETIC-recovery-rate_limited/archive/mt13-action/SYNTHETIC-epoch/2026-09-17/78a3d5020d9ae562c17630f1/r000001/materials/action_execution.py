"""Raw-provider execution adapters; normalization is always re-derived on consume.

strict-open-v1 stays strict. observed-quote-v1 is a SEPARATE valuation scenario,
not proof that a real order was executable. No invented pre-open timestamps.
"""
import ast
import bisect
import json
import math
import re
from datetime import datetime,timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from .timing import instant,positive,digest

MODELS={
 'strict-open-v1':{'price':'first later session open','capture_window_seconds':60,
                   'preflight_max_age_seconds':900,'proof':'preopen CN listed suspension/limit eligibility plus contemporaneous open; HK venue flag unavailable fails closed'},
 'observed-quote-v1':{'price':'new reported trade last-price at actual response receipt; NOT fillability',
                      'max_quote_age_seconds':{'CN':20,'HK':1200},
                      'pair_max_seconds':60,'proof':'two same-session increasing timestamp and volume snapshots',
                      'eligibility':'CN listed suspension/limit gates; HK reported trade progression is valuation proxy, no invented halt/VCM clearance',
                      'settlement':'isolated synthetic inventory; CN T+1; HK same-session resale model; no broker settlement assertion',
                      'no_backdated_open':True,'validated':False}}


def source(ref,sources):
    if ref not in sources:raise ValueError('source_reference_missing')
    meta=sources[ref];raw=Path(meta['path']).read_bytes()
    import hashlib
    if hashlib.sha256(raw).hexdigest()!=meta['sha256']:raise ValueError('source_bytes_changed')
    if instant(meta['started_at'])>instant(meta['fetched_at']):raise ValueError('source_time_order_invalid')
    if meta.get('http_status')!=200:raise ValueError('HTTP_'+str(meta.get('http_status'))+'_retry_next_capture')
    return raw,meta


def api_rows(ref,sources,api):
    raw,m=source(ref,sources)
    if m.get('api')!=api or m.get('url')!='https://api.tushare.pro':raise ValueError('wrong_provider_endpoint')
    d=json.loads(raw)
    if d.get('code')!=0:raise ValueError('provider_code_'+str(d.get('code'))+'_permission_or_rate_retry')
    fields=d['data']['fields'];items=d['data']['items']
    if not isinstance(fields,list) or not isinstance(items,list):raise ValueError('provider_schema_invalid')
    if any(len(x)!=len(fields) for x in items):raise ValueError('provider_row_width_invalid')
    return [dict(zip(fields,r)) for r in items],m


def qt(raw,code):
    num,market=code.split('.');symbol=market.lower()+num
    line=next((l for l in raw.decode('gbk').splitlines() if l.startswith('v_'+symbol+'="')),None)
    if not line:raise ValueError('quote_identity_missing')
    a=line.split('"',2)[1].split('~')
    if a[2]!=num:raise ValueError('quote_foreign_code')
    stamp=datetime.strptime(a[30],'%Y/%m/%d %H:%M:%S' if market=='HK' else '%Y%m%d%H%M%S').replace(tzinfo=ZoneInfo('Asia/Shanghai'))
    out={'code':code,'last':positive(a[3]),'open':positive(a[5]),'volume':float(a[6]),
         'provider_at':stamp.isoformat(), 'high':positive(a[33]),'low':positive(a[34])}
    if not math.isfinite(out['volume']) or out['volume']<0:raise ValueError('invalid_volume')
    if not out['low']<=out['last']<=out['high']:raise ValueError('quote_price_anomaly')
    if market!='HK':out.update(limit_up=positive(a[47]),limit_down=positive(a[48]))
    return out


def read_quote(ref,sources,code):
    raw,m=source(ref,sources)
    if not m.get('url','').startswith('https://qt.gtimg.cn/q='):raise ValueError('wrong_quote_endpoint')
    return qt(raw,code),m


def calendar(ref,sources,market,day):
    api='trade_cal' if market=='CN' else 'hk_tradecal'
    rows,m=api_rows(ref,sources,api)
    target=day.replace('-','')
    matches=[r for r in rows if r['cal_date']==target]
    if len(matches)!=1 or str(matches[0]['is_open']) not in ('0','1'):raise ValueError('calendar_day_unknown')
    nexts=sorted(r['cal_date'] for r in rows if str(r['is_open'])=='1' and r['cal_date']>target)
    return str(matches[0]['is_open'])=='1',(nexts[0] if nexts else None),m


def factor(ref,sources,order,day):
    code=order['code'];signal=order['signal_date'];expected=order['signal_adjusted_price']/order['signal_price']
    if order['market']=='CN':
        rows,m=api_rows(ref,sources,'adj_factor')
        values={r['trade_date']:positive(r['adj_factor']) for r in rows if r['ts_code']==code}
        current=values.get(day.replace('-',''));anchor=values.get(signal.replace('-',''))
    else:
        raw,m=source(ref,sources)
        if m.get('url')!=f'https://finance.sina.com.cn/stock/hkstock/{code.split(".")[0]}/qfq.js':raise ValueError('wrong_factor_endpoint')
        data=ast.literal_eval(raw.decode().split('=',1)[1].split('\n',1)[0].rstrip(';'))['data']
        pairs=sorted((r['d'],positive(r['f'])) for r in data);dates=[r[0] for r in pairs]
        def lookup(d):
            i=bisect.bisect_right(dates,d)-1
            return pairs[i][1] if i>=0 else None
        current,anchor=lookup(day),lookup(signal)
    if not current or not anchor:raise ValueError('current_or_signal_factor_missing_retry_provider')
    if abs(anchor/expected-1)>1e-8:raise ValueError('factor_anchor_revised_new_review_required')
    return current,m


def derive(order,refs,sources,model):
    """Fully deterministic extraction: input envelope cannot supply prices/flags."""
    code,market=order['code'],order['market'];cfg=MODELS[model]
    quote,qm=read_quote(refs['quote'],sources,code)
    capture=instant(qm['fetched_at']);local=capture.astimezone(ZoneInfo('Asia/Shanghai'));day=str(local.date())
    opening=local.replace(hour=9,minute=30,second=0,microsecond=0)
    result={'provider_adapter':'tencent-raw-v1','execution_model':model,'code':code,'market':market,
            'target_signal_id':order['signal_id'],'date':day,'observed_at':qm['fetched_at'],
            'open_at':opening.isoformat(),'source_sha256':qm['sha256'],'refs':refs,
            'basis_id':order['basis_id'],'provider_at':quote['provider_at'],'open':quote['open'],
            'quote_fields':quote,'field_sources':{
                'identity':'Tencent tilde[2] + symbol','open':'Tencent tilde[5]','last':'Tencent tilde[3]',
                'volume':'Tencent tilde[6]','provider_at':'Tencent tilde[30]',
                'received_at':'HTTP response capture clock, never provider timestamp',
                'CN_limits':'Tushare stk_limit + Tencent tilde[47]/[48]; NOT HK fields',
                'factor':'Tushare adj_factor / Sina qfq.js absolute f, anchored to original signal',
                'calendar':'Tushare trade_cal / hk_tradecal is_open; never weekdays'},
            'not_real_order':True,'reason':None}
    try:
        opened,next_day,cm=calendar(refs['calendar'],sources,market,day)
        result['next_session']=next_day
        if not opened:raise ValueError('market_closed_next_verified_session')
        if not (opening<=local<local.replace(hour=11 if market=='CN' else 12,minute=30 if market=='CN' else 0,second=0,microsecond=0)):
            # This bounded collector intentionally covers morning CTS only.
            raise ValueError('outside_morning_capture_session_retry_next_session')
        if day<=order['signal_date'] or capture<=instant(order['triggered_at']):raise ValueError('quote_not_after_signal')
        provider=instant(quote['provider_at'])
        if provider.date()!=local.date() or provider<opening or provider>capture+timedelta(seconds=2):
            raise ValueError('stale_or_future_provider_timestamp')
        f,fm=factor(refs['factor'],sources,order,day);result['factor']=f
        pre=[cm,fm]
        if market=='CN':
            sus,sm=api_rows(refs['suspensions'],sources,'suspend_d')
            if sm['params'].get('trade_date')!=day.replace('-','') or sm['params'].get('suspend_type')!='S':raise ValueError('suspension_snapshot_wrong_day_or_filter')
            if any(r['ts_code']==code for r in sus):raise ValueError('CN_listed_suspended_retry_next_session')
            limits,lm=api_rows(refs['limits'],sources,'stk_limit')
            rr=[r for r in limits if r['ts_code']==code and r['trade_date']==day.replace('-','')]
            if len(rr)!=1:raise ValueError('CN_limits_missing_retry_provider')
            up,down=positive(rr[0]['up_limit']),positive(rr[0]['down_limit'])
            if abs(up-quote['limit_up'])>.011 or abs(down-quote['limit_down'])>.011:raise ValueError('CN_limit_sources_disagree')
            price=quote['open'] if model=='strict-open-v1' else quote['last']
            if not down<=price<=up:raise ValueError('CN_price_outside_limits')
            if (order['side']=='BUY' and price>=up) or (order['side']=='SELL' and price<=down):raise ValueError('CN_side_limit_blocked')
            pre += [sm,lm]
        if any(instant(m['fetched_at'])>capture for m in pre):raise ValueError('eligibility_source_after_quote')
        if model=='strict-open-v1':
            if capture>opening+timedelta(seconds=60):raise ValueError('strict_60_second_window_missed_retry_next_session')
            if any(not opening-timedelta(seconds=900)<=instant(m['fetched_at'])<=opening for m in pre):
                raise ValueError('strict_preopen_eligibility_not_captured_retry_next_session')
            if market=='HK':raise ValueError('HK_strict_preopen_venue_status_unavailable_use_separate_model_or_verified_feed')
            if quote['volume']<=0:raise ValueError('strict_no_reported_trade')
            result.update(simulation_price=quote['open'],simulation_at=opening.isoformat(),
                          eligibility_known_at=max(m['fetched_at'] for m in pre),
                          assumption='CN vendor-listed preopen status; not broker fill guarantee')
        else:
            if capture-provider>timedelta(seconds=cfg['max_quote_age_seconds'][market]):raise ValueError('provider_quote_too_old_retry')
            previous,pm=read_quote(refs['previous_quote'],sources,code)
            if not timedelta(0)<capture-instant(pm['fetched_at'])<=timedelta(seconds=cfg['pair_max_seconds']):raise ValueError('quote_pair_timing_invalid_retry')
            if instant(previous['provider_at'])<opening or instant(previous['provider_at'])>=provider or previous['volume']>=quote['volume']:
                raise ValueError('no_new_reported_trade_retry_next_poll')
            result.update(simulation_price=quote['last'],simulation_at=qm['fetched_at'],
                          eligibility_known_at=qm['fetched_at'],
                          assumption='observed (HK possibly delayed) trade-print valuation; no actual executability, halt-clear or VCM-clear claim',
                          venue_halt_status='unknown_not_asserted',vcm_status='unknown_not_asserted',
                          quote_delay_seconds=(capture-provider).total_seconds())
    except (KeyError,ValueError,TypeError,IndexError,SyntaxError) as e:
        result['reason']=str(e)
    return result


def validate_quote(q,order,asof,sources):
    try:
        if q.get('execution_model')!=order.get('execution_model','strict-open-v1'):return 'execution_model_mismatch'
        if q['target_signal_id']!=order['signal_id']:return 'execution_signal_mismatch'
        expected=derive(order,q['refs'],sources,q['execution_model'])
        if q!=expected:return 'provider_derived_fields_mismatch'
        if instant(q['observed_at'])>instant(asof):return 'execution_future_capture'
        if instant(q.get('simulation_at',q['observed_at']))<=instant(order['triggered_at']):return 'execution_before_attempt_confirmation'
        return q['reason']
    except (KeyError,ValueError,TypeError,IndexError,OSError):return 'provider_evidence_invalid'
