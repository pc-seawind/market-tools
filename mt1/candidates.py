"""New quality/value factors remain shadow; never replace production scoring."""
from datetime import date, datetime
import math


def day(value):
    value=str(value)
    return datetime.strptime(value,'%Y%m%d').date() if len(value)==8 else date.fromisoformat(value)


def number(value):
    try:
        f=float(value)
        return f if math.isfinite(f) else None
    except (ValueError,TypeError): return None


def screen(snapshot):
    asof=day(snapshot['asof']); candidates=[]; excluded=[]; covered=0
    for r in snapshot.get('observations',[]):
        code=r['stock']['ts_code']; reasons=[]
        daily=r.get('daily') or {}
        try:
            if day(daily['trade_date']) != asof: reasons.append('stale_daily')
        except (KeyError,ValueError): reasons.append('missing_daily_date')
        financials=[]
        for f in r.get('financials',[]):
            try:
                # Same-day announcement has no publication time: usable next day only.
                if day(f['ann_date']) < asof and day(f['end_date']) <= day(f['ann_date']):
                    financials.append(f)
            except (ValueError,KeyError,TypeError): pass
        f=max(financials,key=lambda x:(day(x['end_date']),day(x['ann_date']))) if financials else {}
        if not f: reasons.append('no_public_financials_before_asof')
        elif (asof-day(f['end_date'])).days > 550: reasons.append('stale_financials')
        vals={k:number((daily if k in ('pe_ttm','pb','turnover_rate') else f).get(k))
              for k in ('pe_ttm','pb','turnover_rate','roe','ocfps','eps','debt_to_assets')}
        reasons += ['missing_'+k for k,v in vals.items() if v is None]
        if not reasons:
            covered+=1
            # Exploratory parameters, versioned and not promoted to production.
            if 'ST' in r['stock'].get('name','').upper(): reasons.append('special_treatment')
            if not (vals['roe']>=10 and vals['ocfps']>0 and vals['eps']>0): reasons.append('quality_filter')
            if not (0<vals['pe_ttm']<=25 and 0<vals['pb']<=3): reasons.append('value_filter')
            if vals['turnover_rate']<=0: reasons.append('liquidity_filter')
            if vals['debt_to_assets']>70: reasons.append('leverage_filter')
        if reasons:
            excluded.append({'code':code,'reasons':reasons})
        else:
            candidates.append({'code':code,'name':r['stock']['name'],'origin':'quality_value_shadow',
                'qualification':'candidate','method_status':'shadow','channel':'VALUE',
                'financial_public_date':f['ann_date'],'financial_period':f['end_date'],
                'asof':snapshot['asof'],'metrics':vals,'data_version':snapshot['data_version']})
    n=snapshot.get('universe_size',0)
    return {'status':'shadow','method_version':'qv-shadow-1','asof':snapshot['asof'],
            'universe_size':n,'observed':len(snapshot.get('observations',[])),
            'complete_data_count':covered,'coverage':covered/n if n else 0,
            'candidates':candidates,'excluded':excluded,'collection_errors':snapshot.get('errors',[]),
            'limitations':['当前存续股票池，不是历史成分','行业参数尚未校准','财报修订历史不完整，不支持精确历史复刻',
                           '重大事件/技术/价格条件仍需中期证据复核；不生成最终BUY']}
