"""MT13 唯一入口：collect/run/observe/weekly/cancel/register-policy/demo。

隔离事件账本；所有提交均为 longitudinal 不可变快照，manifest 是唯一提交点。
不调用交易 API，不写 scope / 自选 / 真实持仓。输入 contract 见 docs/mt13。
"""
import argparse
import copy
import fcntl
import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import timing
from .longitudinal import archive, manifests, weekly_index
from .scope import DEFAULT, load, codes
from .timing_cli import collect as base_collect, file_hash, protected_hashes

POLICY = Path(__file__).resolve().parents[1] / 'docs/mt13/signal-policy-v1.json'
TERMINAL = {'filled', 'expired', 'cancelled'}


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def read(path):
    return json.loads(Path(path).read_text())


def now():
    return datetime.now(timezone.utc).isoformat()


def write(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    raw = value if isinstance(value, str) else dump(value) + '\n'
    with path.open('x') as f:
        f.write(raw)


def snapshots(root):
    results = []
    for _, path, m in manifests(root):
        if m['job'] != 'mt13-action':
            continue
        base = Path(path).parent
        if file_hash(base/m['result']['path']) != m['result']['sha256']:
            raise ValueError('archive_result_corrupt')
        for material in m['materials']:
            if material.get('blob') and file_hash(base/material['blob']) != material['sha256']:
                raise ValueError('archive_material_corrupt')
        results.append((path, read(base/m['result']['path'])))
    return sorted(results, key=lambda x: x[1]['sequence'])


def fresh():
    return {'sequence': 0, 'ledger': {}, 'positions': {}, 'states': {}, 'cards': [],
            'policies': {}, 'asof': None, 'closed': [], 'transitions': []}


def gap(field, reason, remedy='recollect_next_completed_session'):
    return {'field': field, 'reason': reason, 'remedy_status': remedy}


def exit_only(panel, state, asof):
    """Minimal frozen-risk path: volume/RS/MA entry dependencies never veto SELL.

    No freshly invented stop. Price/factor/basis/calendar/identity remain hard.
    A non-breach cannot prove HOLD if ATR/other exits could not be recomputed.
    """
    if not state or not state.get('monitor'):
        raise ValueError('frozen_exit_reference_missing')
    if panel['code'] != state['code'] or panel['basis_id'] != state['basis_id']:
        raise ValueError('exit_identity_or_basis_changed')
    if panel.get('adjustment') != 'vendor_factor_verified' or not panel.get('calendar_verified'):
        raise ValueError('exit_adjustment_or_calendar_unknown')
    tz = ZoneInfo('Asia/Hong_Kong' if panel['market'] == 'HK' else 'Asia/Shanghai')
    if panel['calendar_coverage_through'] < str(timing.instant(asof).astimezone(tz).date()):
        raise ValueError('exit_calendar_refresh_required')
    if timing.instant(panel['fetched_at']) > timing.instant(asof):
        raise ValueError('exit_future_source')
    b = panel['bars'][-1]
    if b.get('code', panel['code']) != state['code']:
        raise ValueError('exit_foreign_bar')
    if b['date'] != panel['expected_date'] or b['date'] != panel['sessions'][-1] or b['date'] < state['last_date']:
        raise ValueError('exit_latest_completed_price_missing')
    if timing.instant(b['close_at']) > timing.instant(asof) or str(timing.instant(b['close_at']).astimezone(tz).date()) != b['date']:
        raise ValueError('exit_unfinished_price')
    f = timing.positive(b['factor'])
    vals = {k: timing.positive(b[k])*f for k in ('open','high','low','close')}
    if not vals['low'] <= min(vals['open'],vals['close']) <= max(vals['open'],vals['close']) <= vals['high']:
        raise ValueError('exit_price_anomaly')
    # Validate overlapping price/factor values separately from missing volume.
    old = state.get('exit_price_hashes', {})
    overlap = [r for r in panel['bars'] if r['date'] in old]
    if not overlap or any(price_hash(r) != old[r['date']] for r in overlap):
        raise ValueError('exit_history_overlap_missing_or_revised')
    m = state['monitor']; reasons = []
    if vals['close'] < m['structure_low']:
        reasons.append('initial_structure_invalidated')
    if m.get('atr_stop') is not None and vals['close'] < m['atr_stop']:
        reasons.append('ATR_trailing_close_breach_frozen_previous_line')
    if m.get('risk_trigger'):
        reasons = m['risk_trigger']['reasons']
    if not reasons:
        raise ValueError('other_exit_dependencies_unavailable_no_HOLD_claim')
    return reasons, {'structure': m['structure_low']/f,
                     'atr_stop': m.get('atr_stop')/f if m.get('atr_stop') else None}


def price_hash(b):
    return timing.digest({k:b[k] for k in ('date','open','high','low','close','factor')})


def decide(panel, asof, previous, held, cfg):
    r = timing.evaluate(panel, asof=asof, previous=previous,
                        channel=panel.get('channel','TREND'), cfg=cfg['timing'])
    hard = []; risks = []; lines = {}
    if r['status'] == 'ok':
        risks = (r['risk'].get('original_trigger') or {}).get('reasons', [])
        lines = {'structure': r['structure_price'], 'atr_stop': r['atr_stop']}
        r['state']['exit_price_hashes'] = {b['date']:price_hash(b) for b in panel['bars']}
    else:
        try:
            risks, lines = exit_only(panel, previous, asof)
        except (KeyError, ValueError, TypeError, IndexError) as e:
            hard = [gap('timing_input', x) for x in r['gaps'] if not x.endswith('_RS_unknown')]
            hard.append(gap('exit_dependencies', str(e)))
    entries = [k for k,v in r.get('entry',{}).items() if isinstance(v,dict) and v.get('status') == 'trigger']
    if risks:
        action = 'SELL' if held else 'WAIT'
        reason = risks if held else ['risk_prohibits_new_BUY_no_short'] + risks
    elif hard:
        action = 'DATA_BLOCKED'; reason = ['action_specific_hard_data_gap']
    elif entries:
        action = 'BUY'; reason = ['frozen_' + entries[0] + '_trigger']
    else:
        action = 'HOLD' if held else 'WAIT'; reason = ['held_no_exit' if held else 'entry_conditions_not_met']
    return r, {'action':action, 'reasons':reason, 'risk_active':bool(risks),
               'risk_lines':lines, 'hard_gaps':hard,
               'soft_annotations':{'confidence':panel.get('confidence','unknown'),
                                   'efficacy':'experimental_not_validated', 'RS':r.get('rs'),
                                   'confidence_is_veto':False},
               'old_rules':{'entry':r.get('old_entry'), 'exit':r.get('old_exit')},
               'horizons':r.get('horizons',[])}


def transition(s, order, status, reason, at, **extra):
    old = order['execution_status']
    if old == status and order.get('execution_reason') == reason:
        return
    item = {'signal_id':order['signal_id'], 'from':old, 'to':status, 'reason':reason, 'at':at, **extra}
    order.update(execution_status=status, execution_reason=reason)
    order.setdefault('history', []).append(item); s['transitions'].append(item)


def executable(q, order, asof, sources):
    """Separate open snapshot from completed daily bar; no backtest flags.

    Price snapshot <=60s after open; eligibility must have been known by open.
    Missing evidence never manufactures a fill at a closing/stop price.
    """
    try:
        if q['code'] != order['code'] or q['market'] != order['market']:
            return 'execution_identity_mismatch'
        opening = timing.instant(q['open_at']); observed = timing.instant(q['observed_at'])
        if opening <= timing.instant(order['triggered_at']) or q['date'] <= order['signal_date']:
            return 'must_be_later_session_open'
        if not opening <= observed <= opening + timedelta(seconds=60) or observed > timing.instant(asof):
            return 'noncontemporaneous_or_future_open_snapshot'
        if timing.instant(q['eligibility_known_at']) > opening:
            return 'eligibility_not_known_at_open'
        if q['source_sha256'] not in sources or timing.instant(sources[q['source_sha256']]['fetched_at']) > timing.instant(asof):
            return 'execution_source_unverified'
        if timing.instant(sources[q['source_sha256']]['fetched_at']) > opening + timedelta(seconds=60):
            return 'open_source_not_captured_contemporaneously'
        raw_snapshot=read(sources[q['source_sha256']]['path'])
        if raw_snapshot != {k:v for k,v in q.items() if k!='source_sha256'}:
            return 'execution_snapshot_source_content_mismatch'
        if q['session_verified'] is not True or q['halted'] is not False:
            return 'suspended_or_session_unknown'
        if q['settlement_ok'] is not True:
            return 'settlement_unknown_or_ineligible'
        if order['market'] == 'CN':
            if q['limit_up'] not in (True,False) or q['limit_down'] not in (True,False):
                return 'CN_limit_information_unknown'
            if (order['side']=='BUY' and q['limit_up']) or (order['side']=='SELL' and q['limit_down']):
                return 'CN_side_limit_blocked'
        elif order['market'] == 'HK':
            if q['vcm_clear'] is not True:
                return 'HK_VCM_unknown_or_blocked'
        else:
            return 'unsupported_execution_market'
        timing.positive(q['open']); timing.positive(q['factor'])
        if q['basis_id'] != order['basis_id']:
            return 'execution_factor_basis_changed'
        return None
    except (KeyError, ValueError, TypeError, OSError):
        return 'execution_required_fields_missing'


def execute(s, order, quotes, asof, sources, policy):
    if order['execution_status'] in TERMINAL:
        return
    key = order['position_key']; position = s['positions'].get(key)
    if order['side']=='SELL' and not position:
        transition(s,order,'blocked','no_virtual_position_real_holding_not_seeded',asof)
        return
    matching = sorted([q for q in quotes if q.get('code')==order['code']],key=lambda q:q.get('open_at',''))
    reason = 'next_legal_open_snapshot_not_collected'
    for q in matching:
        reason = executable(q,order,asof,sources)
        if reason:
            continue
        if position and order['market']=='CN' and q['date'] <= position['entry_date']:
            reason='CN_T_plus_1'; continue
        price=timing.positive(q['open'])*timing.positive(q['factor'])
        cost=policy['timing']['execution']['per_side_cost_bps'][order['market']]/10000
        fill={'date':q['date'],'at':q['open_at'],'raw_price':q['open'],'adjusted_price':price,
              'source_sha256':q['source_sha256'],'snapshot_sha256':timing.digest(q),
              'virtual_units':1,'not_real_trade':True,'cost_bps_scenario':cost*10000}
        if order['side']=='BUY':
            if position:
                transition(s,order,'cancelled','one_virtual_lot_already_held',asof); return
            s['positions'][key]={'code':order['code'],'version':order['version'], 'entry_signal':order['signal_id'],
                                'entry_date':q['date'],'entry_price':price,'cost':cost,'peak':price,
                                'max_drawdown':0,'marks':{}, 'basis_id':order['basis_id'],
                                'synthetic':order['synthetic'],'scope_epoch':order['scope_epoch']}
        else:
            s['closed'].append({**position, 'exit_signal':order['signal_id'],'exit_date':q['date'],
                                'exit_price':price,'net_return':price*(1-cost)/(position['entry_price']*(1+position['cost']))-1,
                                'post_exit':{},'false_breakout':'registered_breakout_failed' in order['original_reasons'],
                                'personal_pnl':None})
            del s['positions'][key]
            s['states'].pop(key,None)  # next forward virtual episode, no historical reentry
        order['fill']=fill
        transition(s,order,'filled','isolated_simulation_next_observed_open',asof,fill=fill)
        return
    transition(s,order,'pending' if not matching else 'blocked',reason,asof)


def mark_positions(s, panel, version):
    key=version+'|'+panel['code']; rows=panel.get('bars',[])
    for pos in [s['positions'][key]] if key in s['positions'] else []:
        if pos['basis_id'] != panel.get('basis_id'):
            continue
        for b in rows:
            if b['date'] < pos['entry_date'] or b['date'] in pos['marks']:
                continue
            price=b['close']*b['factor']; low=b['low']*b['factor']
            pos['peak']=max(pos['peak'],price)
            pos['max_drawdown']=min(pos['max_drawdown'],low/pos['peak']-1)
            pos['marks'][b['date']]={'price':price,'source_sha256':timing.digest(b)}
    for order in s['ledger'].values():
        if order['position_key'] != key or order['basis_id'] != panel.get('basis_id'):
            continue
        review = order.setdefault('review_prices', {})
        for b in rows:
            if b['date'] > order['signal_date'] and b['date'] not in review:
                review[b['date']] = {'adjusted_close':b['close']*b['factor'], 'source_sha256':timing.digest(b)}
    for pos in s['closed']:
        if pos['version']!=version or pos['code']!=panel['code'] or pos['basis_id']!=panel.get('basis_id'):
            continue
        for b in rows:
            if b['date']>pos['exit_date'] and b['date'] not in pos['post_exit']:
                pos['post_exit'][b['date']]={'return':b['close']*b['factor']/pos['exit_price']-1,'source_sha256':timing.digest(b)}


def daily_report(s):
    lines=['**第三段｜技术策略信号（模拟跟踪，非自动实盘）**',
           '前两段行情播报与趋势/观点对照由原日报原样保留。公司研究与技术动作独立；不指令真实金额/股数。',
           '合成演示，非真实行情/成交。' if s['synthetic'] else '真实只读数据；虚拟成交不代表真实持仓操作，成本未知不算个人盈亏。',
           '|代码/名称|技术动作/适用对象|公司研究（独立）|信号回执/模拟执行|规则/风险线|硬缺口|',
           '|---|---|---|---|---|---|']
    for c in s['cards']:
        refs=[s['ledger'][sid] for sid in c['signal_ids']]
        execution='; '.join(o['signal_id'][:12]+':'+o['side']+'/'+o['execution_status']+'/'+o['execution_reason'] for o in refs) or '无待执行信号'
        lines.append('|'+ '|'.join([c['code']+' '+str(c.get('name','')),c['action']+'/'+c['applies_to'],
                                  c['company_research_status'],execution,','.join(c['reasons'])+'/'+dump(c['risk_lines']),
                                  dump(c['hard_gaps']) if c['hard_gaps'] else '无'])+'|')
        lines.append('\n'+c['code']+'：'+dump({k:c[k] for k in ('version','triggered_at','price_basis','validity','next_review','change','source_sha256','soft_annotations','old_rules')}))
    return '\n'.join(lines)+'\n'


def save(root, s, bundle, scope_path, run_id, materials=()):
    s['sequence']+=1; s['run_id']=run_id; s['status']='recorded'
    events=[]
    for o in s['ledger'].values():
        events.append({'kind':'mt13-signal','origin_id':o['signal_id'],
                       'status':'closed' if o['execution_status'] in TERMINAL else 'pending',
                       'original_date':o['signal_date'], 'original_judgment':dump(o['original_reasons']),
                       'reference_price':o['signal_price'], 'next_review_date':str(timing.instant(s['asof']).date()+timedelta(days=1)),
                       'signal':o['side'], 'execution_status':o['execution_status'], 'version':o['version']})
    mats=[{'name':'bundle.json','content':dump(bundle)},
          {'name':'scope.json','path':str(scope_path)},
          {'name':'daily-third-section.md','content':daily_report(s)},
          {'name':'policies.json','content':dump(s['policies'])},
          {'name':'action_loop.py','path':__file__},
          {'name':'timing.py','path':str(Path(__file__).with_name('timing.py'))}]+list(materials)
    return archive(root=root,job='mt13-action',run_id=run_id,trade_date=s['asof'][:10],scope_epoch=s['scope_epoch'],
                   result=s,materials=mats,events=events,
                   execution={'status':'isolated_simulation_only','started_at':s['asof'],'completed_at':now()})


def observe(bundle_path, scope_path, root, policy_path=POLICY):
    root=Path(root); root.mkdir(parents=True,exist_ok=True)
    with (root/'.action.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        return _observe(bundle_path,scope_path,root,policy_path)


def _observe(bundle_path, scope_path, root, policy_path):
    bundle=read(bundle_path); scope=load(scope_path); cfg=read(policy_path)
    synthetic=bundle.get('source_kind')=='SYNTHETIC_ONLY'
    asof=bundle['asof']; timing.instant(asof)
    if not synthetic:
        if abs((timing.instant(now())-timing.instant(asof)).total_seconds())>1800:
            raise ValueError('live_bundle_stale_recollect_no_historic_order_backfill')
    if timing.instant(asof)<timing.instant(cfg['effective_at']):
        raise ValueError('policy_not_effective_no_retroactive_version')
    if bundle['scope_epoch']!=scope['epoch'] or set(bundle['scope_codes'])!=codes(scope):
        raise ValueError('scope_changed_recollect')
    panels=bundle['panels']
    if len(panels)!=len(codes(scope)) or {p['code'] for p in panels}!=codes(scope):
        raise ValueError('scope_panel_missing_duplicate_or_foreign')
    if bundle.get('contract_hash') != timing.digest(cfg['timing']):
        raise ValueError('bundle_timing_contract_mismatch')
    sources={}; materials=[]
    for i,item in enumerate(bundle.get('inputs',[])):
        if file_hash(item['path'])!=item['sha256']:
            raise ValueError('source_bytes_changed')
        sources[item['sha256']]=item
        materials.append({'name':f'source-{i:03}.raw','path':item['path'],'fetched_at':item['fetched_at']})
    if not synthetic and not sources:
        raise ValueError('real_sources_required')
    prior=snapshots(root); s=copy.deepcopy(prior[-1][1]) if prior else fresh()
    # Same source/policy/scope/code = same transaction, even after later days.
    run_id=timing.digest([bundle, file_hash(scope_path),cfg,file_hash(__file__),file_hash(Path(__file__).with_name('timing.py'))])[:24]
    for path,result in prior:
        if result['run_id']==run_id:
            return {'manifest':path,'idempotent':True}
    if prior and (s['scope_epoch']!=scope['epoch'] or s['synthetic']!=synthetic):
        raise ValueError('new_scope_or_synthetic_requires_separate_root')
    if s['asof'] and timing.instant(asof)<timing.instant(s['asof']):
        raise ValueError('out_of_order_no_backfill')
    version=cfg['version']; old=s['policies'].get(version)
    if old and old!=cfg:
        raise ValueError('frozen_policy_mutation_forbidden')
    if not old and version!='signal-policy-v1':
        registration=Path(root)/'policy-registrations'/(version+'.json')
        if not registration.exists() or read(registration)!=cfg:
            raise ValueError('new_version_requires_forward_registration')
    if version=='signal-policy-v1' and cfg!=read(POLICY):
        raise ValueError('v1_parameters_are_frozen')
    s['policies'][version]=cfg
    before=protected_hashes(scope_path)
    old_cards={c['version']+'|'+c['code']:c for c in s['cards']}
    s.update(asof=asof,scope_epoch=scope['epoch'],synthetic=synthetic,cards=[],transitions=[])
    items={**scope['candidates'],**scope['recommendations'],**scope['holdings']}
    for panel in panels:
        code=panel['code']; key=version+'|'+code
        real_held=code in scope['holdings']; held=real_held or key in s['positions']
        prev=s['states'].get(key)
        r,c=decide(panel,asof,prev,held,cfg)
        if r['status']=='ok':
            s['states'][key]=r['state']
        # A newly known close exit cannot retroactively cancel yesterday's legal
        # open. Process eligible earlier snapshots first, then today's signal.
        for order in s['ledger'].values():
            if order['position_key']!=key or order['execution_status'] in TERMINAL:
                continue
            future=[d for d in panel.get('sessions',[]) if d>order['signal_date']]
            eligible=[q for q in bundle.get('execution_quotes',[]) if q.get('code')==code and q.get('date') in future[:cfg['order_ttl_sessions']]]
            execute(s,order,eligible,asof,sources,cfg)
            if order['execution_status'] not in TERMINAL and len(future)>=cfg['order_ttl_sessions']:
                transition(s,order,'expired','three_completed_sessions_validity_elapsed',asof)
        if r['status']=='ok':
            mark_positions(s,panel,version)
        held=real_held or key in s['positions']
        if c['risk_active']:
            c['action']='SELL' if held else 'WAIT'
            for order in s['ledger'].values():
                if order['position_key']==key and order['side']=='BUY' and order['execution_status'] not in TERMINAL:
                    transition(s,order,'cancelled','risk_overrides_unexecuted_BUY',asof)
        elif c['action'] in ('WAIT','HOLD'):
            c['action']='HOLD' if held else 'WAIT'
        date=panel.get('expected_date')
        if c['action'] in ('BUY','SELL'):
            side=c['action']
            # A single risk episode retains one SELL across days/expiry/cancel.
            episode=(r.get('risk',{}).get('original_trigger') or {}).get('date')
            if side=='SELL':
                episode=(prev or {}).get('action_exit_episode') or episode or date
                if key in s['states']:
                    s['states'][key]['action_exit_episode']=episode
            if side=='BUY':
                episode=date
            sid=timing.digest([scope['epoch'],version,code,side,episode])
            active=[o for o in s['ledger'].values() if o['position_key']==key and o['side']==side and o['execution_status'] not in TERMINAL]
            if sid not in s['ledger'] and not active:
                b=panel['bars'][-1]
                s['ledger'][sid]={'signal_id':sid,'scope_epoch':scope['epoch'],'version':version,
                                 'policy_sha256':timing.digest(cfg),'code':code,'market':panel['market'],
                                 'side':side,'episode':episode,'signal_date':date,'triggered_at':asof,
                                 'condition_known_at':b['close_at'],'signal_price':b['close'], 'signal_adjusted_price':b['close']*b['factor'],
                                 'basis_id':panel['basis_id'],'position_key':key,
                                 'original_reasons':c['reasons'],'risk_lines':c['risk_lines'],
                                 'source_sha256':timing.digest(panel),'synthetic':synthetic,
                                 'execution_status':'pending','execution_reason':'await_later_legal_open',
                                 'history':[],'not_real_order':True,'ttl_sessions':cfg['order_ttl_sessions']}
                if side=='SELL' and key not in s['positions']:
                    transition(s,s['ledger'][sid],'blocked','no_virtual_position_real_holding_not_seeded',asof)
                elif side=='BUY' and key in s['positions']:
                    transition(s,s['ledger'][sid],'cancelled','add_observation_one_virtual_lot_already_held',asof)
        refs=[o['signal_id'] for o in s['ledger'].values()
              if o['position_key']==key and
              (o['execution_status'] not in TERMINAL or o['signal_date']==date)]
        # Always show the latest receipt, including fills/cancellations.
        all_refs=[o['signal_id'] for o in s['ledger'].values() if o['position_key']==key]
        if all_refs and all_refs[-1] not in refs:
            refs.append(all_refs[-1])
        c.update(code=code,name=items[code].get('name',''),version=version,signal_ids=refs,
                 applies_to='现持仓增持观察（非首次买入）' if real_held and c['action']=='BUY' else '现持仓风险观察' if real_held else '隔离模拟仓' if held else '已准入候选',
                 company_research_status=items[code].get('company_research_status','未提供/不由技术信号推断'),
                 triggered_at=asof,price_basis='completed_raw_close; internal=raw*vendor_factor',
                 validity='signal immutable; unfilled order 3 completed sessions; next legal observed open only',
                 next_review='next_completed_market_session; pending execution requires contemporaneous open collector',
                 source_sha256=timing.digest(panel),source_files=list(sources),quote_date=date,
                 close=panel.get('bars', [{}])[-1].get('close') if panel.get('bars') else None,
                 change={'from':old_cards.get(key,{}).get('action'),'to':c['action']},
                 remediation=bundle.get('errors',[]),personal_pnl=None)
        s['cards'].append(c)
    s['protected_unchanged']=before==protected_hashes(scope_path)
    if not s['protected_unchanged']:
        raise ValueError('protected_inputs_changed_during_run')
    return save(root,s,bundle,scope_path,run_id,materials)


def signal_reviews(orders):
    result=[]
    for order in orders:
        prices=sorted(order.get('review_prices',{}).items())
        result.append({'signal_id':order['signal_id'],'side':order['side'],
                       'horizons':{str(h):({'status':'observed_price_only',
                                           'return':prices[h-1][1]['adjusted_close']/order['signal_adjusted_price']-1,
                                           'date':prices[h-1][0],'source_sha256':prices[h-1][1]['source_sha256']}
                                          if len(prices)>=h else {'status':'not_matured','return':None})
                                   for h in (20,40,60)},'not_personal_pnl':True})
    return result


def weekly(root, asof):
    cutoff=timing.instant(asof)
    rows=[r for _,r in snapshots(root) if timing.instant(r['asof'])<=cutoff]
    if not rows:
        raise ValueError('no_observations_before_cutoff')
    s=rows[-1]; start=cutoff-timedelta(days=7); versions={}
    for version,cfg in s['policies'].items():
        orders=[o for o in s['ledger'].values() if o['version']==version]
        new=[o for o in orders if start<timing.instant(o['triggered_at'])<=cutoff]
        closed=[p for p in s['closed'] if p['version']==version]
        positions=[p for p in s['positions'].values() if p['version']==version]
        horizons={}
        for h in (20,40,60):
            mature=[]
            for p in positions+closed:
                marks=sorted((d,v) for d,v in p['marks'].items() if d>p['entry_date'])
                if len(marks)>=h:
                    mature.append({'code':p['code'],'return':marks[h-1][1]['price']/p['entry_price']-1,
                                   'source_sha256':marks[h-1][1]['source_sha256']})
            horizons[str(h)]={'effective_samples':len(mature),'observations':mature,'gap':'not_matured_or_position_exited_before_horizon' if len(mature)<len(positions+closed) else None}
        pending=[{'signal_id':o['signal_id'],'code':o['code'],'side':o['side'],'reason':o['execution_reason']} for o in orders if o['execution_status'] not in TERMINAL]
        versions[version]={'policy':cfg,'BUY':sum(o['side']=='BUY' for o in new),'SELL':sum(o['side']=='SELL' for o in new),
                           'total_signals':len(orders),'cross_week_pending':pending,
                           'cancelled':sum(o['execution_status']=='cancelled' for o in orders),
                           'expired':sum(o['execution_status']=='expired' for o in orders),
                           'positions':positions,'exits':closed,'effective_round_trips':len(closed),
                           'net_returns':[p['net_return'] for p in closed],
                           'path_drawdowns':[p['max_drawdown'] for p in positions+closed],
                           'false_breakouts':sum(p['false_breakout'] for p in closed),
                           'post_exit_rally':[max([v['return'] for v in p['post_exit'].values()],default=None) for p in closed],
                           'horizons':horizons,'signal_reviews':signal_reviews(orders),
                           'zero_trade_explanation':'see hard_gaps / entry_conditions_not_met / execution pending; confidence never vetoes',
                           'gaps':['small_forward_sample_not_efficacy_proof','costs_are_scenarios','daily_low_close_path_not_intrabar_order'],
                           'automatic_parameter_change':False}
    return {'asof':asof,'synthetic':s['synthetic'],'scope_epoch':s['scope_epoch'], 'versions':versions,
            'comparison':'按版本分账向前比较；旧MT12规则每日报并列观察，不虚构旧规则成交收益',
            'next_iteration':'人工提交reason/parent/version/frozen timing/effective_at；保留v1向前并行，禁止回填与自动调参',
            'longitudinal':weekly_index(root,asof=asof,current_epoch=s['scope_epoch'])}


def register_policy(root, path):
    cfg=read(path); prior=snapshots(root); s=prior[-1][1] if prior else fresh()
    if cfg['version']=='signal-policy-v1' or not cfg.get('reason') or cfg.get('parent') not in s['policies']:
        raise ValueError('new_version_reason_parent_required')
    if timing.instant(cfg['effective_at'])<=timing.instant(s['asof']):
        raise ValueError('new_version_must_start_forward')
    if cfg.get('approval')!='experimental_signal_use_not_validated' or cfg['order_ttl_sessions']<1:
        raise ValueError('experimental_contract_required')
    if Path(cfg['version']).name!=cfg['version']:
        raise ValueError('invalid_version')
    write(Path(root)/'policy-registrations'/(cfg['version']+'.json'),cfg)
    return {'registered':cfg['version'],'effective_at':cfg['effective_at'],'not_validated':True}


def cancel(root, scope_path, sid, reason):
    if not reason:
        raise ValueError('cancellation_reason_required')
    root=Path(root)
    with (root/'.action.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        prior=snapshots(root); s=copy.deepcopy(prior[-1][1]); order=s['ledger'][sid]
        if load(scope_path)['epoch']!=s['scope_epoch']:
            raise ValueError('scope_changed')
        if order['execution_status'] in TERMINAL:
            return {'idempotent':True,'status':order['execution_status']}
        s['asof']=max(now(),s['asof'],key=timing.instant);s['transitions']=[]
        transition(s,order,'cancelled',reason,s['asof'])
        return save(root,s,{'cancel':sid,'reason':reason},scope_path,timing.digest([sid,reason,'cancel'])[:24])


def main():
    ap=argparse.ArgumentParser(description=__doc__); sub=ap.add_subparsers(dest='cmd',required=True)
    for name in ('collect','run','observe'):
        p=sub.add_parser(name);p.add_argument('--scope',default=DEFAULT);p.add_argument('--policy',default=str(POLICY))
        if name in ('run','observe'):p.add_argument('--root',required=True)
        if name=='observe':p.add_argument('--bundle',required=True)
        else:p.add_argument('--out',required=True)
    p=sub.add_parser('weekly');p.add_argument('--root',required=True);p.add_argument('--asof',required=True);p.add_argument('--out',required=True)
    p=sub.add_parser('register-policy');p.add_argument('--root',required=True);p.add_argument('--policy',required=True)
    p=sub.add_parser('cancel');p.add_argument('--root',required=True);p.add_argument('--scope',default=DEFAULT);p.add_argument('--signal-id',required=True);p.add_argument('--reason',required=True)
    p=sub.add_parser('demo');p.add_argument('--out',required=True)
    a=ap.parse_args()
    if a.cmd in ('collect','run'):
        from .action_collect import collect
        result=collect(a.scope,a.out)
        if a.cmd=='run':result=observe(result['bundle'],a.scope,a.root,a.policy)
    elif a.cmd=='observe':result=observe(a.bundle,a.scope,a.root,a.policy)
    elif a.cmd=='weekly':result=weekly(a.root,a.asof);write(a.out,result);result={'out':a.out,'sha256':file_hash(a.out)}
    elif a.cmd=='register-policy':result=register_policy(a.root,a.policy)
    elif a.cmd=='cancel':result=cancel(a.root,a.scope,a.signal_id,a.reason)
    else:
        from .action_demo import demo
        result=demo(a.out)
    print(dump(result))


if __name__=='__main__':
    main()
