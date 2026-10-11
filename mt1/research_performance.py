"""Read-only episode performance, NOT discovery-cohort or executed portfolio P&L.

Dates/benchmark/costs are frozen in the ORIGINAL recommendation/exit event.
Inputs are hash-verified archived series + explicit corporate-action coverage.
No price substitution, adjusted/unadjusted mixing, interpolation, or promotion.
"""
import hashlib
import json
import math
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


def day(s):
    return date.fromisoformat(s)


def finite(value, positive=False):
    if isinstance(value, bool):
        raise ValueError('boolean numeric value')
    n = float(value)
    if not math.isfinite(n) or (positive and n <= 0):
        raise ValueError('invalid numeric value')
    return n


def validate_contract(c, reviewed=None):
    if c.get('basis') != 'unadjusted_close' or c.get('anchor_policy') != 'close_observation_not_execution':
        raise ValueError('explicit unadjusted close observation basis required')
    start = day(c['anchor_date'])
    dates = [day(d) for d in c['target_dates']]
    if not dates or dates != sorted(set(dates)) or any(d <= start for d in dates):
        raise ValueError('unique increasing target dates after anchor required')
    if reviewed is not None and start < reviewed.astimezone(ZoneInfo('Asia/Shanghai')).date():
        raise ValueError('retroactive performance baseline forbidden')
    if c.get('currency') not in ('CNY', 'HKD'):
        raise ValueError('only CNY/HKD close observations currently supported')
    if not c.get('benchmark_code') or not c.get('fee_scenario'):
        raise ValueError('currency/benchmark/fee scenario required')
    for key in ('entry_bps', 'exit_bps'):
        if not 0 <= finite(c['fees'][key]) < 10000:
            raise ValueError('invalid explicit cost scenario')


def read_bundle(scope_path):
    path = Path(str(scope_path) + '.research-performance-input.json')
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text())
        if not isinstance(value, dict): raise ValueError('performance bundle must be an object')
        return value
    except (OSError, ValueError) as e:
        return {'input_error': str(e)}


def _archive(spec, now, manifest):
    raw = Path(spec['path']).read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    if sha != spec['sha256']:
        raise ValueError('source hash mismatch')
    fetched = datetime.fromisoformat(spec['fetched_at'].replace('Z', '+00:00'))
    if fetched.tzinfo is None or fetched > now or not spec.get('url'):
        raise ValueError('source provenance/time missing or future')
    value = json.loads(raw)
    if not isinstance(value, dict): raise ValueError('archived source must be an object')
    manifest.append({'path': str(Path(spec['path']).resolve()), 'sha256': sha,
                     'url': spec['url'], 'fetched_at': spec['fetched_at']})
    return value


def _series(bundle, code, currency, asof, now, manifest):
    specs = bundle.get('series', [])
    if not isinstance(specs, list) or any(not isinstance(s, dict) for s in specs):
        raise ValueError('malformed price series list')
    matching = [s for s in specs if s.get('code') == code]
    if len(matching) != 1:
        raise ValueError('price series missing or duplicate: ' + code)
    data = _archive(matching[0]['source'], now, manifest)
    if data.get('code') != code or data.get('currency') != currency or data.get('basis') != 'unadjusted_close':
        raise ValueError('price identity/currency/basis mismatch: ' + code)
    fetched = datetime.fromisoformat(matching[0]['source']['fetched_at'].replace('Z', '+00:00')).astimezone(ZoneInfo('Asia/Shanghai'))
    fetched_day = fetched.date()
    rows = {}
    for r in data['rows']:
        if not isinstance(r, dict): raise ValueError('malformed price row')
        d = day(r['date'])
        if r['date'] in rows or d > asof or d > fetched_day:
            raise ValueError('duplicate/future price row: ' + code)
        if d == fetched_day and fetched.hour < 17:
            raise ValueError('same-day close captured before conservative CN/HK completion')
        rows[r['date']] = r
    return data, rows


def _actions(bundle, code, start, end, now, manifest):
    specs = bundle.get('corporate_actions', [])
    if not isinstance(specs, list) or any(not isinstance(s, dict) for s in specs):
        raise ValueError('malformed corporate action list')
    matching = [s for s in specs if s.get('code') == code]
    if len(matching) != 1:
        raise ValueError('corporate actions unverified: ' + code)
    data = _archive(matching[0]['source'], now, manifest)
    if (data.get('code') != code or data.get('status') != 'verified' or
            day(data['from']) > start or day(data['through']) < end):
        raise ValueError('corporate action coverage missing: ' + code)
    # An empty verified list is affirmative evidence, not absence of a file.
    if not isinstance(data.get('events'), list):
        raise ValueError('corporate action list missing')
    fetched_day = datetime.fromisoformat(matching[0]['source']['fetched_at'].replace('Z', '+00:00')).astimezone(ZoneInfo('Asia/Shanghai')).date()
    if not day(data['from']) <= day(data['through']) <= fetched_day:
        raise ValueError('corporate action coverage invalid/future')
    for e in data['events']:
        if not isinstance(e, dict) or not day(data['from']) <= day(e['date']) <= day(data['through']):
            raise ValueError('corporate action outside verified coverage')
        if start < day(e['date']) <= end:
            raise ValueError('corporate action requires dedicated adapter: ' + code + ':' + str(e.get('type')))


def _interval(bundle, c, code, start, end, asof, now, manifest):
    prices = {}
    for symbol in (code, c['benchmark_code']):
        data, rows = _series(bundle, symbol, c['currency'], asof, now, manifest)
        if data.get('kind') == 'index' and symbol == c['benchmark_code']:
            if data.get('index_type') != 'price_index':
                raise ValueError('benchmark must be explicit price index')
        else:
            _actions(bundle, symbol, day(start), day(end), now, manifest)
        selected = []
        for d in (start, end):
            r = rows.get(d)
            if r is None or r.get('status') != 'traded':
                raise ValueError('exact-date traded price missing/suspended/delisted: ' + symbol + ':' + d)
            selected.append(finite(r['close'], positive=True))
        prices[symbol] = selected
    a, b = prices[code]
    ba, bb = prices[c['benchmark_code']]
    entry, exit_ = finite(c['fees']['entry_bps'])/10000, finite(c['fees']['exit_bps'])/10000
    gross = b/a - 1
    net = b*(1-exit_)/(a*(1+entry)) - 1
    benchmark_gross = bb/ba - 1
    benchmark_net = bb*(1-exit_)/(ba*(1+entry)) - 1
    return {'status': 'verified_price_scenario', 'reason': None,
            'anchor_price': a, 'end_price': b, 'gross_return': gross, 'net_return': net,
            'benchmark_anchor_price': ba, 'benchmark_end_price': bb,
            'benchmark_gross_return': benchmark_gross, 'benchmark_net_return': benchmark_net,
            'excess_net_return': net-benchmark_net}


def evaluate(pool, bundle=None, now=None):
    now = now or datetime.now(timezone.utc)
    local_now = now.astimezone(ZoneInfo('Asia/Shanghai'))
    actual_day = local_now.date()
    results, manifest = [], []
    bundle_error = None
    asof = None
    try:
        if not isinstance(bundle, dict):
            raise ValueError('performance observation bundle missing or malformed')
        if bundle.get('scope_epoch') != pool['scope_epoch']:
            raise ValueError('performance bundle epoch mismatch')
        if bundle.get('input_error'):
            raise ValueError(bundle['input_error'])
        asof = day(bundle['asof'])
        if asof > actual_day:
            raise ValueError('future observation asof')
    except (ValueError, KeyError, TypeError) as e:
        bundle_error = str(e)
    def observation(ep, event, contract_key, kind, contract=None, targets=None):
        c = contract if contract is not None else event.get(contract_key)
        base = {'episode_id': ep['episode_id'], 'code': ep['code'], 'kind': kind,
                'anchor_event_id': event['request_id'], 'scope_epoch': ep['scope_epoch'],
                'anchor_date': c.get('anchor_date') if isinstance(c, dict) else None,
                'contract': c, 'gross_return': None, 'net_return': None, 'excess_net_return': None,
                'benchmark_gross_return': None, 'benchmark_net_return': None,
                'personal_pnl': None, 'executed_trade': False}
        try:
            validate_contract(c, datetime.fromisoformat(event['reviewed_at'].replace('Z', '+00:00')))
        except (ValueError, KeyError, TypeError, AttributeError) as e:
            results.append({**base, 'target_date': None, 'status': 'unknown', 'reason': 'original performance contract missing/invalid: '+str(e)})
            return
        for target in targets if targets is not None else c['target_dates']:
            item = {**base, 'target_date': target, 'status': 'blocked', 'reason': bundle_error}
            if day(target) > actual_day or (day(target) == actual_day and local_now.hour < 17):
                item.update(status='not_matured', reason='target date/complete CN-HK close not reached')
            elif bundle_error is None and day(target) > asof:
                item['reason'] = 'observations do not cover matured target'
            elif bundle_error is None:
                try:
                    item.update(_interval(bundle, c, ep['code'], c['anchor_date'], target, asof, now, manifest))
                except (ValueError, KeyError, TypeError, OSError) as e:
                    item['reason'] = str(e)
            results.append(item)
    for ep in pool.get('episodes', []):
        if ep['scope_epoch'] != pool['scope_epoch']:
            continue
        rec, exit_ = ep['recommendation'], ep['exit']
        if rec:
            c = rec.get('performance_contract')
            # Do not roll post-exit prices into a recommendation holding return.
            exit_date = ((exit_ or {}).get('performance_exit') or {}).get('anchor_date') if isinstance((exit_ or {}).get('performance_exit', {}), dict) else None
            if c and exit_:
                try:
                    validate_contract(c, datetime.fromisoformat(rec['reviewed_at'].replace('Z', '+00:00')))
                except (ValueError, KeyError, TypeError, AttributeError):
                    observation(ep, rec, 'performance_contract', 'recommendation_horizon')
                    observation(ep, exit_, 'performance_exit', 'post_exit_counterfactual')
                    continue
                try:
                    validate_contract(exit_['performance_exit'], datetime.fromisoformat(exit_['reviewed_at'].replace('Z', '+00:00')))
                except (ValueError, KeyError, TypeError, AttributeError):
                    exit_date = None
                for target in c.get('target_dates', []):
                    if exit_date and day(target) <= day(exit_date):
                        observation(ep, rec, 'performance_contract', 'recommendation_horizon', targets=[target])
                    else:
                        results.append({'episode_id': ep['episode_id'], 'code': ep['code'],
                            'kind': 'recommendation_horizon', 'anchor_event_id': rec['request_id'],
                            'anchor_date': c.get('anchor_date'), 'target_date': target,
                            'status': 'closed_before_horizon' if exit_date else 'unknown',
                            'reason': 'explicit exit; no implied continued position' if exit_date else 'exit price date missing',
                            'net_return': None, 'gross_return': None, 'excess_net_return': None})
                if exit_date and day(exit_date) >= day(c['anchor_date']):
                    observation(ep, rec, 'performance_contract', 'recommendation_to_exit', targets=[exit_date])
            else:
                observation(ep, rec, 'performance_contract', 'recommendation_horizon')
        if exit_:
            observation(ep, exit_, 'performance_exit', 'post_exit_counterfactual')
    return {'schema_version': 1, 'scope_epoch': pool['scope_epoch'], 'evaluated_at': now.isoformat(),
            'price_asof': bundle.get('asof') if isinstance(bundle, dict) else None, 'results': results,
            'sources': list({(m['path'], m['sha256']): m for m in manifest}.values()),
            'episode_count': len(pool.get('episodes', [])), 'input_error': bundle_error,
            'definition': '未复权收盘价情景；显式双边费用同样作用于股票与基准；非总回报、非真实成交/个人盈亏；退出后为继续持有反事实而非持仓。',
            'read_only': True, 'discovery_cohort_used': False}


def render(value):
    names = {'recommendation_horizon': '推荐期间', 'recommendation_to_exit': '推荐至退出',
             'post_exit_counterfactual': '退出后反事实'}
    def pct(n):
        return '待核验' if n is None else f'{n*100:.2f}%'
    lines = ['**研究 episode 绩效核验（不是实盘盈亏）**', value['definition'],
             '|代码/episode|核验段|基准日期→目标日期|状态/原因|毛/净价格收益|基准净收益/超额|',
             '|---|---|---|---|---|---|']
    contracts = []
    for r in value['results']:
        lines.append(f"|{r['code']} / {r['episode_id']}|{names[r['kind']]}|{r.get('anchor_date')}→{r.get('target_date')}|{r['status']}：{r.get('reason') or '已核验原文数据及公司行动区间'}|{pct(r.get('gross_return'))} / {pct(r.get('net_return'))}|{pct(r.get('benchmark_net_return'))} / {pct(r.get('excess_net_return'))}|")
        c = r.get('contract')
        if isinstance(c, dict) and all(k in c for k in ('benchmark_code','currency','basis','fee_scenario','fees')) and isinstance(c['fees'], dict) and all(k in c['fees'] for k in ('entry_bps','exit_bps')):
            contracts.append(f"基准 {c['benchmark_code']}；币种 {c['currency']}；价格口径 {c['basis']}；费用 {c['fee_scenario']}：买入 {c['fees']['entry_bps']} bps / 卖出 {c['fees']['exit_bps']} bps。")
    if not value['results']:
        lines.append('|无已签审推荐/退出episode|无样本|—|不借用发现cohort|待核验|待核验|')
    lines += ['', *dict.fromkeys(contracts)]
    return '\n'.join(lines)+'\n'
