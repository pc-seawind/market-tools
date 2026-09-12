"""Frozen execution recovery v1. Signals/reasons unchanged; attempts append-only."""
from datetime import timedelta
from .timing import instant,digest

POLICY={'version':'execution-recovery-v1','attempt_ttl_completed_sessions':3,
        'renew':'expired SELL + virtual position + recent confirmed persistent risk',
        'max_confirmation_age_days':4,'cancel':'never auto resume explicit cancel',
        'pause':'explicit operator pause; explicit resume only',
        'price':'never backfill before attempt confirmation','signal_id_unchanged':True}


def ensure(order):
    order.setdefault('attempts',[{'number':1,'opened_at':order['triggered_at'],
                                 'after_session':order['signal_date'],'status':'pending',
                                 'reason':'original_signal','recovery_policy':POLICY['version']}])
    return order['attempts'][-1]


def renew(s,order,asof):
    from .action_loop import transition
    a=ensure(order);c=order.get('risk_reconfirmation',{})
    if (order['side']!='SELL' or order['execution_status']!='expired'
            or order.get('operator_paused') or order['position_key'] not in s['positions']
            or not c.get('active')):return False
    age=instant(asof)-instant(c['known_at'])
    if age<timedelta(0) or age>timedelta(days=POLICY['max_confirmation_age_days']):return False
    if instant(asof)<=instant(a.get('ended_at',order['triggered_at'])):return False
    order['attempts'].append({'number':len(order['attempts'])+1,'opened_at':asof,
                             'after_session':c['date'],'status':'pending',
                             'reason':'persistent_SELL_reconfirmed','confirmation_sha256':digest(c),
                             'recovery_policy':POLICY['version']})
    transition(s,order,'pending','persistent_SELL_new_execution_attempt_same_signal',asof,
               attempt=len(order['attempts']),confirmation=c)
    return True


def expire(s,order,at,date):
    from .action_loop import transition
    a=ensure(order);a.update(status='expired',ended_at=at,ended_session=date)
    transition(s,order,'expired','three_completed_sessions_validity_elapsed',at,attempt=a['number'])
