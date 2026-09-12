"""Deterministic matched forward signals. Never consume claimed pass/count/PnL.

Frames must be recomputed from archived engine/source stages by the caller.
Historical bars inside a live panel are NEVER forward observations: only its
latest completed bar, captured after registration, is one observed session.
"""
from collections import defaultdict
from statistics import mean
from .timing import digest, instant, positive
from .iteration_policy import CONTRACT


def validate(frames, category, baseline, candidate, *, kind, asof):
    minimum=CONTRACT['min_independent_events']; window=CONTRACT['primary_sessions']
    if kind not in ('REAL_CURRENT','SYNTHETIC_ONLY'):raise ValueError('unknown_evidence_kind')
    cutoff=instant(asof); seen={}; bycode=defaultdict(list)
    for f in frames:
        if f['kind']!=kind or f['contract_hash']!=digest(CONTRACT):raise ValueError('mixed_kind_or_contract')
        if instant(f['observed_at'])>cutoff:raise ValueError('future_frame')
        if f['category']!=category:continue
        if f['baseline']!=baseline or f['candidate']!=candidate:continue
        if len(f['scope_codes'])!=len(set(f['scope_codes'])):raise ValueError('duplicate_scope')
        if f['capital_per_code']!=CONTRACT['capital_per_code'] or f['cost_bps']!=CONTRACT['per_side_cost_bps']:
            raise ValueError('unequal_capital_or_cost')
        scope=digest(sorted(f['scope_codes']))
        frame_hash=digest(f)  # Once per frame, not O(universe^2) serialized bytes.
        for row in f['rows']:
            if row['code'] not in f['scope_codes']:raise ValueError('foreign_code')
            if instant(row['close_at'])>instant(f['observed_at']):raise ValueError('future_price')
            # This check rejects stale new frames, not old genuine forward history.
            if (instant(f['observed_at'])-instant(row['close_at'])).days>CONTRACT['max_source_age_days']:
                raise ValueError('expired_price_frame')
            if row['market'] not in f['cost_bps']:raise ValueError('unmatched_market')
            positive(row['price'])
            key=(scope,row['code'],row['date'])
            identity={k:row[k] for k in ('price','basis','close_at','market','baseline_selected','candidate_selected')}
            if key in seen:
                if seen[key]!=identity:raise ValueError('revised_same_session_evidence')
                continue
            seen[key]=identity
            bycode[(scope,row['code'])].append({**row,'observed_at':f['observed_at'], 'frame_hash':frame_hash})
    pairs=[]; pending=[]; diagnostics={str(h):0 for h in CONTRACT['horizons']}
    for (scope,code), values in sorted(bycode.items()):
        rows=sorted(values,key=lambda r:r['date']); next_origin=0
        for i,origin in enumerate(rows):
            if i<next_origin:continue
            if not origin['baseline_selected'] and not origin['candidate_selected']:continue
            later=[r for r in rows[i+1:] if instant(r['close_at'])>instant(origin['observed_at'])]
            # No jumping over missing sessions by merely counting observations.
            if any(r.get('previous_session')!=p['date'] for p,r in zip([origin]+later,later)):
                pending.append({'code':code,'reason':'forward_session_gap','origin':origin['date']});continue
            for h in CONTRACT['horizons']:
                if len(later)>=h+1:diagnostics[str(h)]+=1
            if len(later)<window+1:
                pending.append({'code':code,'origin':origin['date'],'observed_sessions':len(later),
                                'reason':'not_matured','required':window+1});continue
            path=later[:window+1]
            if any(r['basis']!=origin['basis'] or r['market']!=origin['market'] for r in path):
                raise ValueError('factor_basis_or_market_changed')
            cost=CONTRACT['per_side_cost_bps'][origin['market']]/10000
            def metrics(selected):
                if not selected:return {'net':0.,'drawdown':0.,'tail':0.,'turnover':0.}
                prices=[positive(r['price']) for r in path]
                peak=prices[0]*(1+cost); dd=0.; daily=[]
                for j,p in enumerate(prices):
                    value=p*(1-cost if j==len(prices)-1 else 1)
                    dd=min(dd,value/peak-1);peak=max(peak,value)
                    if j:daily.append(prices[j]/prices[j-1]-1)
                return {'net':prices[-1]*(1-cost)/(prices[0]*(1+cost))-1,
                        'drawdown':dd,'tail':min(daily),'turnover':2.}
            pairs.append({'code':code,'scope_hash':scope,'origin':origin['date'],
                          'start':path[0]['date'],'end':path[-1]['date'],
                          'baseline':metrics(origin['baseline_selected']),
                          'candidate':metrics(origin['candidate_selected']),
                          'evidence_hash':digest([origin,path])})
            # Disjoint per-stock events. Identical frames cannot inflate N.
            next_origin=rows.index(path[-1])+1
    result={'category':category,'baseline':baseline,'candidate':candidate,'kind':kind,'contract_hash':digest(CONTRACT),
            'asof':asof,'independent_events':len(pairs),'pairs':pairs,'pending':pending,
            'horizon_counts_diagnostic':diagnostics,'decision':'continue_shadow',
            'reason':'insufficient_mature_forward_samples','metric_type':CONTRACT['evaluation'],
            'execution_evidence_is_separate':True,'input_hash':digest(frames)}
    if len(pairs)>=minimum:
        old={k:mean(p['baseline'][k] for p in pairs) for k in ('net','drawdown','tail','turnover')}
        new={k:mean(p['candidate'][k] for p in pairs) for k in old}
        # Worst event risk too, not only average risk which can mask tail losses.
        risk=all(min(p['candidate'][k] for p in pairs)>=min(p['baseline'][k] for p in pairs) for k in ('drawdown','tail'))
        improved=new['net']>old['net'] and new['drawdown']>=old['drawdown'] and new['tail']>=old['tail'] and risk
        result.update(baseline_metrics=old,candidate_metrics=new,
                      decision='experimental_activate' if improved else 'reject',
                      reason='matched_signal_forward_improvement_not_production_validation' if improved else 'no_improvement_or_risk_worse')
    return result
