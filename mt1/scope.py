"""Read-only administrative scope overlay; never rewrites strategy history.

Library calls without MT1_TRACKING_SCOPE retain historical/test semantics.
Production CLI always sets an explicit path and fails closed before side effects.
"""
import json
import os
from datetime import datetime
from pathlib import Path

DEFAULT = '/home/emox/work/investment/reference/tracking-scope.json'


def load(path=None):
    path = path or os.environ.get('MT1_TRACKING_SCOPE')
    if not path:
        return None
    try:
        data = json.loads(Path(path).read_text())
        epoch = data.get('epoch') or data.get('scope_epoch')
        reset = datetime.fromisoformat(data['reset_at'].replace('Z', '+00:00'))
        assert epoch and reset.tzinfo
        holdings = data.get('holdings', data.get('confirmed_holdings'))
        assert isinstance(holdings, list) and isinstance(data['active_candidates'], list)
        def normalize(items, candidate=False):
            result = {}
            for item in items:
                assert isinstance(item, dict)
                code = item.get('code') or item.get('ticker') or item.get('ts_code')
                assert isinstance(code, str) and code and code not in result
                if candidate:
                    admitted = datetime.fromisoformat(item['admitted_at'].replace('Z', '+00:00'))
                    assert admitted.tzinfo and admitted >= reset and item['research_event_id']
                    assert item.get('scope_epoch', epoch) == epoch
                result[code] = {**item, 'code': code}
            return result
        return {'epoch': epoch, 'reset_at': data['reset_at'],
                'holdings': normalize(holdings), 'candidates': normalize(data['active_candidates'], True),
                'recommendations': normalize(data.get('active_recommendations', []), True)}
    except Exception as e:
        raise ValueError(f'tracking_scope_invalid_fail_closed: {path}: {e}') from e


def codes(scope):
    return set(scope['holdings']) | set(scope['candidates']) | set(scope['recommendations'])


def filter_plans(plans, scope=None):
    scope = scope if scope is not None else load()
    if scope is None:
        return plans
    admitted={**scope['candidates'], **scope['recommendations']}
    def allowed(p):
        code=p.get('code')
        if code in scope['holdings']:
            return True
        item=admitted.get(code)
        return bool(item and p.get('scope_epoch')==scope['epoch']
                    and p.get('research_event_id')==item['research_event_id'])
    return [{**p, 'holding_status': 'confirmed' if p['code'] in scope['holdings'] else 'not_held',
             'holding_evidence': {'source': 'user_tracking_scope', 'epoch': scope['epoch']},
             'actual_cost': None, 'scope_epoch': scope['epoch']}
            for p in plans if allowed(p)]


def counts(scope=None):
    scope = scope if scope is not None else load()
    return {} if scope is None else {'scope_epoch': scope['epoch'], 'confirmed_holdings': len(scope['holdings']),
                                   'active_candidates': len(scope['candidates']), 'active_recommendations': len(scope['recommendations']), 'raw_scan_is_separate': True}
