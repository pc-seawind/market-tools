"""MT14 bounded experiment contract. Never authorizes production validation."""
import copy
import math
from pathlib import Path
from .action_loop import POLICY, read
from .timing import digest

SCHEMA = 'mt14-v1'
KINDS = ('REAL_CURRENT', 'SYNTHETIC_ONLY')
BASE = {'fundamental': {'roe_min': 10.0, 'pe_max': 25.0, 'pb_max': 3.0},
        'technical': {'breakout_volume_min': 1.2}}
BOUNDS = {'fundamental': {'roe_min': (10, 14), 'pe_max': (20, 25), 'pb_max': (2, 3)},
          'technical': {'breakout_volume_min': (1.2, 1.6)}}
# Validator reads this module, NOT a candidate-supplied contract/flags/counts.
CONTRACT = {'schema': SCHEMA, 'max_candidates': 2, 'horizons': [20, 40, 60],
            'primary_sessions': 60, 'min_independent_events': 20,
            'max_source_age_days': 4, 'capital_per_code': 10000,
            'per_side_cost_bps': {'CN': 15, 'HK': 25, 'US': 10},
            'minimum_net_improvement': 0.0, 'drawdown_worsening_allowed': 0.0,
            'tail_worsening_allowed': 0.0, 'production_activation': False,
            'evaluation': 'matched_forward_signal_cash_slots_not_fill_or_personal_PnL',
            'historical_current_universe_is_PIT': False}


def parameters(category, overrides=None):
    if category not in BASE:
        raise ValueError('unknown_category')
    overrides = overrides or {}
    if not isinstance(overrides, dict) or set(overrides)-set(BOUNDS[category]):
        raise ValueError('non_whitelisted_parameter_or_contract_mutation')
    cfg = {**BASE[category], **overrides}
    for key, value in cfg.items():
        lo, hi = BOUNDS[category][key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not lo <= value <= hi:
            raise ValueError('parameter_out_of_bounds:' + key)
    return cfg


def candidate(category, overrides, reason, evidence_hash, effective_at):
    if not reason or not evidence_hash:
        raise ValueError('candidate_requires_reason_and_evidence')
    p = parameters(category, overrides)
    return {'id': category+'-'+digest(p)[:12], 'category': category, 'parameters': p,
            'reason': reason, 'evidence_hash': evidence_hash, 'effective_at': effective_at,
            'contract_hash': digest(CONTRACT), 'status': 'experimental_not_validated'}


def action_policy(c):
    cfg = copy.deepcopy(read(POLICY))
    if c is None:
        return cfg
    parameters(c['category'], c['parameters'])
    if c['category'] != 'technical' or c['contract_hash'] != digest(CONTRACT):
        raise ValueError('invalid_technical_candidate')
    cfg.update(version=c['id'], parent=cfg['version'], effective_at=c['effective_at'], reason=c['reason'])
    cfg['timing'].update(c['parameters'])
    return cfg


def code_hashes():
    from .timing_cli import file_hash
    root = Path(__file__).parent
    paths = list(root.glob('iteration_*.py')) + [root/n for n in (
        'candidates.py', 'action_loop.py', 'action_execution.py', 'action_recovery.py', 'timing.py')]
    return {p.name: file_hash(p) for p in sorted(paths)}
