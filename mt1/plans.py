"""Separate research direction from actual holding confirmation; immutable episodes."""
from datetime import date
import math
from urllib.parse import urlparse
from .store import digest

STATES = {'BUY', 'WATCH', 'HOLD', 'SELL', 'EXIT'}
CHANNELS = {'VALUE', 'TREND', 'REVERSAL', 'UNKNOWN'}
IMMUTABLE = ('plan_id', 'code', 'market', 'episode', 'original_reason', 'original_date',
             'reference_price', 'original_deadline', 'legacy_source')


def reduce_plan(old, patch, method_lookup=None, *, legacy_import=False):
    new = {**(old or {}), **patch}
    if old:
        for k in IMMUTABLE:
            if new.get(k) != old.get(k):
                raise ValueError(f'immutable episode field: {k}')
        if old['state'] == 'EXIT' and new['state'] != 'EXIT':
            raise ValueError('EXIT re-entry requires a new episode')
        if new.get('review_due') != old.get('review_due'):
            reasons = evidence_errors(patch.get('extension_evidence'), date.today())
            if reasons:
                raise ValueError('review changes require valid evidence: '+','.join(reasons))
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
    if cost is not None and (new['holding_status'] != 'confirmed' or type(cost) not in (int,float) or not math.isfinite(cost) or cost <= 0 or not new.get('holding_evidence')):
        raise ValueError('real cost requires positive confirmed cost and user evidence')
    if (new['state'] == 'BUY' or new['unheld_direction'] == 'BUY') and new.get('qualification') != 'final':
        raise ValueError('BUY requires final research qualification')
    exit_transition = new['state'] in ('SELL', 'EXIT') or new['held_direction'] in ('SELL', 'EXIT')
    legacy_exit_import = legacy_import and old is None and new.get('legacy_source') and new.get('legacy_latest_state') == 'EXIT'
    if exit_transition and not legacy_exit_import:
        if new.get('exit_basis') not in ('thesis_invalidated', 'risk_boundary', 'valuation_realized', 'catalyst_realized', 'time_review') or not new.get('evidence'):
            raise ValueError('exit requires substantive evidence, not sector heat')
    if exit_transition and not legacy_exit_import:
        reasons = exit_errors(new, date.today())
        if reasons: raise ValueError('invalid exit: '+','.join(reasons))
    if new.get('deadline_extension') != (old or {}).get('deadline_extension'):
        reasons = deadline_errors(new, date.today())
        if reasons: raise ValueError('invalid extension: '+','.join(reasons))
        extension = new.get('deadline_extension') or {}
        if extension.get('plan_version') != (old or {}).get('version', 0):
            raise ValueError('extension must bind previous plan version')
        previous = (old or {}).get('deadline_extension') or {}
        if previous and extension['deadline'] <= previous['deadline']:
            raise ValueError('extension must advance effective deadline')
    if new.get('qualification') == 'final':
        reasons = qualification_errors(new, date.today(), method_lookup)
        if reasons:
            raise ValueError('not qualified: '+','.join(reasons))
    return new


def evidence_errors(evidence, asof):
    if not isinstance(evidence, list) or not evidence:
        return ['evidence_missing']
    errors = []
    for e in evidence:
        try:
            url = urlparse(e['url'])
            if (url.scheme not in ('http', 'https') or not url.netloc
                    or not isinstance(e['claim'], str) or not e['claim'].strip()
                    or date.fromisoformat(e['date']) > asof):
                errors.append('evidence_invalid_or_future')
        except (KeyError, ValueError, TypeError, AttributeError):
            errors.append('evidence_invalid')
    return errors


def exit_errors(p, asof):
    errors = evidence_errors(p.get('evidence'), asof)
    if not p.get('reviewer'): errors.append('reviewer_missing')
    if p.get('exit_basis') not in ('thesis_invalidated', 'risk_boundary',
            'valuation_realized', 'catalyst_realized', 'time_review'):
        errors.append('exit_basis_invalid')
    return errors


def deadline_errors(p, asof):
    errors = []
    try:
        original = date.fromisoformat(p['original_date'])
        deadline = date.fromisoformat(p['original_deadline'])
        if original > asof or not 0 < (deadline-original).days <= 100:
            errors.append('original_horizon_invalid')
        ext = p.get('deadline_extension')
        if ext:
            new_deadline = date.fromisoformat(ext['deadline'])
            reviewed = date.fromisoformat(ext['reviewed_at'])
            if (new_deadline <= deadline or reviewed > asof or reviewed < original
                    or not 0 < (new_deadline-reviewed).days <= 100
                    or not ext.get('reviewer') or not ext.get('reason')
                    or type(ext.get('plan_version')) is not int or ext['plan_version'] < 0):
                errors.append('extension_invalid')
            errors.extend(evidence_errors(ext.get('evidence'), asof))
            deadline = new_deadline
        if deadline <= asof: errors.append('horizon_due_rejustify')
    except (ValueError, KeyError, TypeError):
        errors.append('horizon_unknown_reconstruct')
    return errors


def qualification_errors(p, asof, method_lookup=None):
    # Exit evidence remains usable when buying conditions, method or term fail.
    buying = p.get('state') == 'BUY' or p.get('unheld_direction') == 'BUY'
    exiting = p.get('state') in ('SELL', 'EXIT') or p.get('held_direction') in ('SELL', 'EXIT')
    if buying and exiting: return ['conflicting_buy_exit']
    if exiting: return exit_errors(p, asof)
    errors = evidence_errors(p.get('evidence'), asof)
    if not p.get('reviewer'): errors.append('reviewer_missing')
    if p.get('channel') not in CHANNELS - {'UNKNOWN'}: errors.append('channel_unknown')
    errors.extend(deadline_errors(p, asof))
    for field in ('risk_boundary', 'invalidation', 'milestones', 'review_due'):
        if not p.get(field): errors.append(field+'_missing')
    try:
        if not 0 <= (date.fromisoformat(p['review_due'])-asof).days <= 31:
            errors.append('review_overdue')
        for m in p.get('milestones', []):
            if not m.get('condition') or not 0 <= (date.fromisoformat(m['date'])-asof).days <= 100:
                errors.append('milestone_invalid')
    except (ValueError, KeyError, TypeError, AttributeError): errors.append('dates_invalid')
    risk = p.get('risk_boundary')
    if not isinstance(risk, dict) or not risk.get('condition') or not risk.get('source_url'):
        errors.append('risk_boundary_unsubstantiated')
    if not buying:
        if p.get('state') == 'HOLD' or p.get('held_direction') == 'HOLD':
            if not p.get('holding_thesis') or p.get('holding_thesis_status') != 'valid':
                errors.append('holding_thesis_not_validated')
        return errors
    price = p.get('price_condition')
    if not isinstance(price, dict) or not price.get('basis') or not price.get('source_url'):
        errors.append('price_condition_unsubstantiated')
    elif not any(type(price.get(k)) in (int,float) and math.isfinite(price[k]) and price[k] > 0 for k in ('below', 'above')):
        errors.append('price_condition_no_valid_level')
    for k in ('financial', 'liquidity', 'major_event', 'technical', 'valuation'):
        if (p.get('checks') or {}).get(k) != 'pass': errors.append(k+'_not_passed')
    method = method_lookup('method:'+p['method_id']) if method_lookup and p.get('method_id') else None
    if not method or method.get('status') != 'active' or not p.get('method_version') or method.get('rule_version') != p['method_version']:
        errors.append('registered_active_method_version_required')
    if p.get('candidate_origin') == 'quality_value_shadow': errors.append('new_factor_shadow_only')
    return errors


def eligible(p, asof, method_lookup=None):
    return (p.get('qualification') == 'final' and p.get('unheld_direction') == 'BUY'
            and p.get('state') == 'BUY' and not qualification_errors(p, asof, method_lookup))


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
