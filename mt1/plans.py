"""Separate research direction from actual holding confirmation; immutable episodes."""
from datetime import date
import math
from .store import digest

STATES = {'BUY', 'WATCH', 'HOLD', 'SELL', 'EXIT'}
CHANNELS = {'VALUE', 'TREND', 'REVERSAL', 'UNKNOWN'}
IMMUTABLE = ('plan_id', 'code', 'market', 'episode', 'original_reason', 'original_date',
             'reference_price', 'original_deadline', 'legacy_source')


def reduce_plan(old, patch):
    new = {**(old or {}), **patch}
    if old:
        for k in IMMUTABLE:
            if new.get(k) != old.get(k):
                raise ValueError(f'immutable episode field: {k}')
        if old['state'] == 'EXIT' and new['state'] != 'EXIT':
            raise ValueError('EXIT re-entry requires a new episode')
        if new.get('review_due') != old.get('review_due') and not patch.get('extension_evidence'):
            raise ValueError('deadline changes require new evidence')
    for field in ('plan_id', 'code', 'market', 'episode', 'original_reason', 'original_date',
                  'original_deadline', 'reference_price', 'channel', 'state', 'holding_status',
                  'unheld_direction', 'held_direction', 'evidence', 'milestones', 'price_condition',
                  'risk_boundary', 'invalidation', 'review_due', 'qualification'):
        if field not in new:
            raise ValueError(f'missing field: {field}')
    if new['state'] not in STATES or new['channel'] not in CHANNELS or new['market'] not in ('CN', 'HK', 'US'):
        raise ValueError('invalid state/channel/market')
    if new['unheld_direction'] not in ('BUY', 'WATCH') or new['held_direction'] not in ('WATCH', 'HOLD', 'SELL', 'EXIT'):
        raise ValueError('invalid independent directions')
    if new['holding_status'] not in ('unknown', 'not_held', 'confirmed'):
        raise ValueError('invalid holding status')
    cost = new.get('actual_cost')
    if cost is not None and (new['holding_status'] != 'confirmed' or not isinstance(cost, (int,float)) or cost <= 0 or not new.get('holding_evidence')):
        raise ValueError('real cost requires positive confirmed cost and user evidence')
    if new['state'] == 'BUY' and new.get('qualification') != 'final':
        raise ValueError('BUY requires final research qualification')
    if (new['state'] in ('SELL', 'EXIT') or new['held_direction'] in ('SELL', 'EXIT')) and not new.get('legacy_source'):
        if new.get('exit_basis') not in ('thesis_invalidated', 'risk_boundary', 'valuation_realized', 'catalyst_realized', 'time_review') or not new.get('evidence'):
            raise ValueError('exit requires substantive evidence, not sector heat')
    if new.get('qualification') == 'final':
        reasons = qualification_errors(new, date.today())
        if reasons:
            raise ValueError('not qualified: '+','.join(reasons))
    return new


def qualification_errors(p, asof):
    errors = []
    if p.get('channel') not in CHANNELS - {'UNKNOWN'}:
        errors.append('channel_unknown')
    for field in ('price_condition', 'risk_boundary', 'invalidation', 'milestones', 'review_due'):
        if not p.get(field):
            errors.append(field+'_missing')
    try:
        if date.fromisoformat(p['review_due']) < asof:
            errors.append('review_overdue')
        for m in p.get('milestones', []):
            d = date.fromisoformat(m['date'])
            if not m.get('condition') or not 0 <= (d-asof).days <= 100:
                errors.append('milestone_invalid')
    except (ValueError, KeyError, TypeError):
        errors.append('dates_invalid')
    price = p.get('price_condition')
    if not isinstance(price, dict) or not price.get('basis') or not price.get('source_url'):
        errors.append('price_condition_unsubstantiated')
    elif not any(isinstance(price.get(k), (int,float)) and math.isfinite(price[k]) and price[k] > 0 for k in ('below', 'above')):
        errors.append('price_condition_no_valid_level')
    risk = p.get('risk_boundary')
    if not isinstance(risk, dict) or not risk.get('condition') or not risk.get('source_url'):
        errors.append('risk_boundary_unsubstantiated')
    evidence = p.get('evidence') or []
    if not evidence:
        errors.append('evidence_missing')
    for e in evidence:
        try:
            if not e.get('url') or not e.get('claim') or date.fromisoformat(e['date']) > asof:
                errors.append('evidence_invalid_or_future')
        except (KeyError, ValueError, TypeError):
            errors.append('evidence_invalid')
    for k in ('financial', 'liquidity', 'major_event', 'technical', 'valuation'):
        if (p.get('checks') or {}).get(k) != 'pass':
            errors.append(k+'_not_passed')
    if not p.get('reviewer') or p.get('method_status') != 'active':
        errors.append('review_or_active_method_missing')
    if p.get('candidate_origin') == 'quality_value_shadow':
        errors.append('new_factor_shadow_only')
    return errors


def eligible(p, asof):
    return (p.get('qualification') == 'final' and p.get('unheld_direction') == 'BUY'
            and p.get('state') == 'BUY' and not qualification_errors(p, asof))


def legacy_plan(record, source, key):
    code = record.get('code') or record.get('ts_code') or record.get('ticker') or key
    market = 'CN' if code.endswith(('.SH', '.SZ', '.BJ')) else ('HK' if code.endswith('.HK') else 'US')
    # Do not infer original entry date/price from latest HOLD or WATCH.
    plan_id = 'legacy-' + digest([source, key])[:24]
    return dict(plan_id=plan_id, code=code, name=record.get('name', ''), market=market,
                episode='legacy-unreconstructed', channel=record.get('channel', 'UNKNOWN') if record.get('channel') in CHANNELS else 'UNKNOWN',
                state='WATCH', original_reason=record.get('reason') or '历史原文见 legacy_source，原始建仓理由未知',
                original_date=None, original_deadline=None, reference_price=None,
                legacy_source={'source': source, 'key': key, 'record': record},
                holding_status='unknown', actual_cost=None, unheld_direction='WATCH', held_direction='WATCH',
                evidence=[], milestones=[], price_condition=None, risk_boundary=None, invalidation=None,
                review_due=None, qualification='pending_review', legacy_latest_state=record.get('action') or record.get('status'))


def personal_return(plan, price):
    cost = plan.get('actual_cost')
    return price/cost-1 if plan.get('holding_status') == 'confirmed' and cost else None
