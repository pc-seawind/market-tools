"""New-ledger forward observation, separate from legacy recs and real P&L.

Unadjusted quoted reference-to-close marks only; not execution, total return,
strategy backtest, or user's return. Missing original baselines remain unknown.
"""
import json
import math
from datetime import date
from pathlib import Path
from .data import HERE, api, atomic_json
from .store import Store, digest


def prices(plan, asof):
    name={'CN':'daily','HK':'hk_daily','US':'us_daily'}[plan['market']]
    return api(name,ts_code=plan['code'],start_date=plan['original_date'].replace('-',''),
               end_date=asof.isoformat().replace('-',''),fields='ts_code,trade_date,close')


def verify(state_dir=None, asof=None, fetch=prices):
    asof=asof or date.today()
    root=Path(state_dir or HERE/'.cron_state/mt1')
    if not (root/'plans.db').exists():return {'status':'no_ledger','verified':0}
    s=Store(root/'plans.db'); marks=[]; unknown=[]; errors=[]
    try:
        # Earliest final BUY per episode, even if latest state is EXIT; never reset
        # original reference on HOLD or exclude exited episodes (survivorship bias).
        entries={}
        for row in s.db.execute("SELECT after_json FROM events WHERE entity LIKE 'plan:%' ORDER BY seq"):
            p=json.loads(row[0])
            if p.get('state')=='BUY' and p.get('qualification')=='final':entries.setdefault(p['plan_id'],p)
        for latest in s.all():
            if latest['plan_id'] not in entries:unknown.append({'plan_id':latest['plan_id'],'reason':'no_final_buy_baseline'})
        for pid,p in entries.items():
            try:
                start=date.fromisoformat(p['original_date']); reference=p['reference_price']
                if type(reference) not in (int,float) or not math.isfinite(reference) or reference<=0 or start>asof:
                    raise ValueError('unknown original reference/date')
                key='verification:'+pid+':'+str(asof)
                prior=s.latest(key)
                if prior:marks.append(prior);continue
                rows=fetch(p,asof); bars={}
                for r in rows:
                    d=r['trade_date'];d=f'{d[:4]}-{d[4:6]}-{d[6:]}' if len(d)==8 else d
                    if r['ts_code']!=p['code'] or not start.isoformat()<d<=asof.isoformat():continue
                    close=float(r['close'])
                    if math.isfinite(close) and close>0:bars[d]=close
                if not bars:raise ValueError('no subsequent dated closes')
                days=sorted(bars); last=days[-1]
                mark={'plan_id':pid,'entry_plan_version':p['version'],'asof':str(asof),
                      'quote_date':last,'reference_price':reference,'close':bars[last],
                      'quoted_return':bars[last]/reference-1,
                      'observed_sessions':len(days),
                      'horizons':{str(n):({'date':days[n-1],'quoted_return':bars[days[n-1]]/reference-1} if len(days)>=n else None) for n in (20,40,60)},
                      'basis':'unadjusted_reference_mark_not_execution_or_total_return',
                      'source':'Tushare dated daily bars','data_hash':digest(rows)}
                marks.append(s.apply(key,0,key,mark,'新账本后验证，非实盘收益',lambda old,p:p))
            except Exception as e:errors.append({'plan_id':pid,'error':str(e)})
        result={'status':'partial' if errors or unknown else 'ok','asof':str(asof),
                'verified':len(marks),'marks':marks,'unknown':unknown,'errors':errors,
                'limitations':['报价未复权，不是策略收益或个人盈亏','20/40/60按有效行情观测计数，停牌/缺行可能延后；非精确交易日回测']}
        atomic_json(root/'verification'/f'{asof}-{digest(result)[:16]}.json',result)
        return result
    finally:s.close()
