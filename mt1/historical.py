"""Offline shadow historical adapters. No network, production writes or promotion.

Verified input envelopes are an evidence contract, not proof of historical truth:
reviewers must audit their local source artifacts and availability attestations.
Never substitute current data, infer suspension from holes, or mint PIT flags.
"""
import argparse
import hashlib
import json
import math
from dataclasses import asdict, fields
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from .store import digest


def day(value):
    text = str(value)
    if not (len(text) == 10 or (len(text) == 8 and text.isdigit())):
        raise ValueError('invalid calendar date')
    return date.fromisoformat(text if '-' in text else f'{text[:4]}-{text[4:6]}-{text[6:8]}').isoformat()


def instant(value):
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        raise ValueError('timezone required')
    return dt


def sessions_checked(sessions):
    result = [day(s) for s in sessions]
    if result != sorted(set(result)):
        raise ValueError('calendar must be unique sorted sessions')
    return result


def positive(value):
    n = float(value)
    if not math.isfinite(n) or n <= 0:
        raise ValueError('invalid positive number')
    return n


def universe(rows, sessions):
    """D2 [list_date, delist_date); unresolved intervals block global completeness.

    A delisting effective date is not a fill/settlement price. Exchange calendar
    and L/D/P source completeness are separately audited by the caller.
    """
    sessions = sessions_checked(sessions)
    intervals = {}; rejected = []
    for row in rows:
        code = row.get('ts_code')
        try:
            if not code or code in intervals:
                raise ValueError('missing/duplicate code')
            start = day(row['list_date'])
            end = day(row['delist_date']) if row.get('delist_date') else None
            if row.get('list_status') == 'D' and not end:
                raise ValueError('delisted date unknown')
            if end and end <= start:
                raise ValueError('invalid listing interval')
            intervals[code] = (start, end)
        except (KeyError, TypeError, ValueError) as e:
            rejected.append({'code': code, 'reason': str(e)})
    by_day = {d: sorted(c for c, (a, b) in intervals.items() if a <= d and (b is None or d < b)) for d in sessions}
    return {'by_day': by_day, 'rejected': rejected, 'intervals': intervals,
            'input_hash': digest(rows), 'calendar_hash': digest(sessions),
            'status': 'blocked' if rejected else 'intervals_built_source_completeness_requires_audit',
            'settlement_verified': False}


def indexed(rows):
    result = {}
    for r in rows:
        key = (r['ts_code'], day(r['trade_date']))
        if key in result:
            raise ValueError('duplicate panel row: ' + str(key))
        result[key] = r
    return result


def holes(universe_by_day, panels, suspension_days):
    """D4 exact anti-join; explicit full-market suspension coverage is necessary.

    suspension_days maps date to complete provider rows (an empty list is valid).
    Missing date != no suspensions. Partial/intraday records are not full-day proof.
    """
    if set(panels) != {'daily', 'adj_factor', 'stk_limit'}:
        raise ValueError('daily/adj_factor/stk_limit panels required')
    maps = {k: indexed(v) for k, v in panels.items()}
    results = []; expected = 0
    for d, codes in sorted(universe_by_day.items()):
        for c in codes:
            expected += 1
            missing = [k for k, m in maps.items() if (c, d) not in m]
            if missing:
                susp = suspension_days.get(d)
                events = [r for r in susp or [] if r.get('ts_code') == c]
                results.append({'code': c, 'date': d, 'missing': missing,
                    'classification': 'suspension_record_needs_review' if events else
                        'unexplained_missing_row' if susp is not None else 'suspension_coverage_unknown',
                    'suspension_events': events})
    return {'expected_symbol_sessions': expected, 'holes': results,
            'hole_count': len(results), 'complete': not results,
            'input_hash': digest([universe_by_day, panels, suspension_days]),
            'note': 'absence is never a zero return or proof of suspension'}


def envelope(value, decision):
    """D1/D5 reject unknown versions, future observations and changed artifacts."""
    available = instant(value['available_at'])
    observed = instant(value['observed_at'])
    if available > decision or observed > decision:
        raise ValueError('future input')
    if observed > available:
        raise ValueError('availability precedes observation')
    if value.get('pit_verified') is not True or not value.get('version_id'):
        raise ValueError('PIT version unknown')
    if digest(value['payload']) != value['payload_hash']:
        raise ValueError('payload hash mismatch')
    # Attestation itself can be retrospective; asserted availability cannot be.
    for key in ('source', 'availability_attestation'):
        ref = value[key]
        if hashlib.sha256(Path(ref['path']).read_bytes()).hexdigest() != ref['sha256']:
            raise ValueError(key + ' hash mismatch')
    return value['payload']


def select_version(versions, decision_at):
    """Choose latest disclosed revision of latest period, never latest cache row.

    Future versions are excluded. Any undated record or eligible unverified
    revision blocks instead of silently falling back to an obsolete revision.
    """
    decision = instant(decision_at)
    for row in versions:
        instant(row['available_at'])
        day(row['period'])
    usable = [r for r in versions if instant(r['available_at']) <= decision and day(r['period']) <= decision.date().isoformat()]
    if not usable:
        raise ValueError('no historical version available')
    keys = [(day(r['period']), instant(r['available_at'])) for r in usable]
    if len(set(keys)) != len(keys):
        raise ValueError('ambiguous financial revision')
    selected = max(usable, key=lambda r: (day(r['period']), instant(r['available_at'])))
    envelope(selected, decision)
    return selected


def rules_version():
    # Evaluation calls only pure sector_picks.evaluate; no latest-data loaders.
    path = Path(__file__).resolve().parents[1] / 'sector_picks.py'
    return hashlib.sha256(path.read_bytes()).hexdigest()


def recompute(bundle):
    """D5 recompute actual three-channel evaluator from frozen as-of inputs.

    Current rule source is explicitly retrospectively replayed, not represented
    as a method active in 2024. No old-verdict mapping and no live-data fallback.
    All StockMetrics fields must be supplied; even optional defaults cannot hide
    absent inputs. Raw-to-feature archival coverage remains a separate blocker.
    """
    result = {'status': 'blocked', 'promotion': 'shadow', 'metrics': None,
              'exact_backtest': False, 'decision_at': bundle.get('decision_at'),
              'bundle_hash': digest(bundle), 'rule_version': rules_version()}
    try:
        from sector_picks import StockMetrics, evaluate
        from etf_data import SectorSignals
        decision = instant(bundle['decision_at'])
        local_day = decision.astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat()
        if bundle['rule_version'] != rules_version():
            raise ValueError('rule version mismatch')
        inputs = {k: envelope(bundle['inputs'][k], decision) for k in
                  ('stock', 'sector', 'context', 'membership', 'financial')}
        stock = inputs['stock']; context = inputs['context']
        # Full-day features are not knowable before the CN close.
        close_at = instant(day(stock['trade_date'])+'T15:00:00+08:00')
        if close_at > decision or close_at > instant(bundle['inputs']['stock']['available_at']):
            raise ValueError('unclosed daily stock features')
        if set(stock) != {f.name for f in fields(StockMetrics)}:
            raise ValueError('incomplete StockMetrics fields')
        if day(stock['trade_date']) > local_day or not stock['fina_as_of'] or day(stock['fina_as_of']) > local_day:
            raise ValueError('future or unknown stock period')
        financial = inputs['financial']
        for k in ('roe', 'gross_margin', 'net_yoy', 'rev_yoy', 'fina_as_of'):
            if stock[k] != financial[k]:
                raise ValueError('financial payload mismatch: ' + k)
        membership = inputs['membership']
        if stock['code'] not in membership['codes'] or membership['concept'] != inputs['sector']['concept']:
            raise ValueError('historical membership mismatch')
        if day(membership['in_date']) > local_day or (membership.get('out_date') and local_day >= day(membership['out_date'])):
            raise ValueError('membership outside effective interval')
        if inputs['sector']['data_quality'] != 'direct':
            raise ValueError('proxy sector inputs forbidden')
        ev = evaluate(StockMetrics(**stock), SectorSignals(**inputs['sector']), **context)
        # Do not re-publish embedded legacy proxy-backtest claims in ev.reason.
        result.update(status='recomputed_shadow', action=ev.action, channel=ev.channel, veto=ev.veto,
            available_at=max((bundle['inputs'][k]['available_at'] for k in bundle['inputs']), key=instant),
            code=stock['code'], method_id='historical-sector-picks-shadow',
            interpretation='retrospective current-rule replay; not historically active or validated',
            input_hashes={k: v['payload_hash'] for k, v in bundle['inputs'].items()})
    except (ValueError, KeyError, TypeError, OSError) as e:
        result['blocker'] = str(e)
    return result


def next_open(code, decision_at, side, sessions, daily, limits, suspension_days,
              factors, fee_rate, slippage_rate, delist_date=None):
    """D6 conservative simulated raw-open fills; not guaranteed exchange fills.

    Both limit boundaries, any suspension event, unknown coverage and holes
    preclude execution. Missing data blocks search (cannot prove first fill).
    Factors are recorded, not silently turned into total-return prices; corporate
    action cash accounting/settlement must be provided before portfolio replay.
    """
    if side not in ('buy', 'sell'):
        raise ValueError('invalid side')
    for n in (fee_rate, slippage_rate):
        if not math.isfinite(n) or not 0 <= n < .1:
            raise ValueError('invalid costs')
    decision = instant(decision_at).astimezone(ZoneInfo('Asia/Shanghai'))
    ds = indexed(daily); ls = indexed(limits); fs = indexed(factors); skipped = []
    for d in sessions_checked(sessions):
        if d <= decision.date().isoformat():
            continue
        if delist_date and d >= day(delist_date):
            return {'status': 'blocked', 'reason': 'delisting settlement unknown', 'skipped': skipped}
        key = (code, d)
        if d not in suspension_days:
            return {'status': 'blocked', 'reason': 'suspension coverage unknown', 'date': d, 'skipped': skipped}
        if any(r.get('ts_code') == code for r in suspension_days[d]):
            skipped.append({'date': d, 'reason': 'suspension event (conservative)'}); continue
        if any(key not in m for m in (ds, ls, fs)):
            return {'status': 'blocked', 'reason': 'missing daily/limit/adjustment row', 'date': d, 'skipped': skipped}
        try:
            bar = ds[key]; limit = ls[key]
            p = positive(bar['open']); hi = positive(bar['high']); lo = positive(bar['low'])
            up = positive(limit['up_limit']); down = positive(limit['down_limit'])
            factor = positive(fs[key]['adj_factor'])
            if not lo <= p <= hi or not down < up:
                raise ValueError('invalid OHLC/limits')
            volume = float(bar['vol'])
            if not math.isfinite(volume) or volume < 0:
                raise ValueError('invalid volume')
            if volume == 0 or p >= up or p <= down:
                skipped.append({'date': d, 'reason': 'zero volume or limit boundary'}); continue
            execution = p * (1 + slippage_rate if side == 'buy' else 1 - slippage_rate)
            if not down < execution < up:
                skipped.append({'date': d, 'reason': 'slippage crosses limit'}); continue
            return {'status': 'simulated_fill', 'date': d, 'side': side, 'raw_open': p,
                'execution_price': execution, 'fee_per_share': execution * fee_rate,
                'cash_per_share': execution * (1 + fee_rate if side == 'buy' else 1 - fee_rate),
                'adj_factor': factor, 'price_basis': 'raw_CNY_not_total_return', 'skipped': skipped,
                'guaranteed_fill': False, 'replay_ready': False}
        except (ValueError, KeyError, TypeError) as e:
            return {'status': 'blocked', 'reason': str(e), 'date': d, 'skipped': skipped}
    return {'status': 'blocked', 'reason': 'no subsequent executable session', 'skipped': skipped}


def price_features(code, decision_at, sessions, daily, factors):
    """D5 pure as-of technical builder; strict 250-session warm-up, no fallback.

    Raw price/factor availability must additionally be attested by input envelopes.
    Reject future rows rather than accidentally using latest adjustment anchors.
    Missing sessions (including suspension) block features until an explicit
    audited suspended-session feature policy exists.
    """
    from sector_picks import _percentile_of, _classify_volume, _classify_pv
    dt = instant(decision_at).astimezone(ZoneInfo('Asia/Shanghai'))
    days = sessions_checked(sessions)
    if not days or days[-1] > dt.date().isoformat() or (days[-1] == dt.date().isoformat() and dt.hour < 15):
        raise ValueError('future/unclosed daily bar')
    ds = indexed(daily); fs = indexed(factors)
    if any(c != code or d > days[-1] for c, d in list(ds) + list(fs)):
        raise ValueError('future or foreign-symbol price input')
    if len(days) < 250:
        raise ValueError('250-session warm-up incomplete')
    closes = []; volumes = []; adjs = []
    if (code, days[-1]) not in fs:
        raise ValueError('adjustment anchor missing')
    anchor = positive(fs[(code, days[-1])]['adj_factor'])
    for d in days:
        if (code, d) not in ds or (code, d) not in fs:
            raise ValueError('price/factor session hole')
        row = ds[(code, d)]; adj = positive(fs[(code, d)]['adj_factor'])
        closes.append(positive(row['close']) * adj / anchor)
        volume = float(row['vol'])
        if not math.isfinite(volume) or volume < 0:
            raise ValueError('invalid volume')
        volumes.append(volume); adjs.append(adj)
    close = closes[-1]; window = closes[-120:]; hi, lo = max(window), min(window)
    if sum(volumes[-6:-1]) <= 0 or sum(volumes[-20:]) <= 0:
        raise ValueError('zero-volume feature window')
    one = volumes[-1] / (sum(volumes[-6:-1])/5)
    five = (sum(volumes[-5:])/5) / (sum(volumes[-20:])/20)
    pct1 = (close/closes[-2]-1)*100; pct5 = (close/closes[-6]-1)*100
    return dict(trade_date=days[-1].replace('-', ''), close=close,
        pct_1d=round(pct1,2), pct_5d=round(pct5,2), pct_1w=round(pct5,2),
        pct_1m=round((close/closes[-21]-1)*100,2),
        position_pct=int(round((close-lo)/(hi-lo)*100)) if hi>lo else 50,
        pct_rank_60d=_percentile_of(closes[-60:],close),
        pct_rank_120d=_percentile_of(window,close), pct_rank_250d=_percentile_of(closes[-250:],close),
        vol_ratio_1d=round(one,2), vol_ratio_5d=round(five,2), vol_ratio=round(five,2),
        volume_signal=_classify_volume(one,five), pv_alignment=_classify_pv(pct1,pct5,one,five),
        qfq_applied=len(set(adjs))>1, adj_events_60d=max(0,len(set(adjs[-60:]))-1),
        dd_from_hi120=round((close/hi-1)*100,1))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['universe', 'holes', 'recompute', 'next-open', 'price-features'])
    parser.add_argument('--input', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    data = json.loads(Path(args.input).read_text())
    funcs = {'universe': universe, 'holes': holes, 'next-open': next_open, 'price-features': price_features}
    result = recompute(data) if args.operation == 'recompute' else funcs[args.operation](**data)
    # New artifacts only; never overwrite a prior evidence file.
    with Path(args.output).open('x') as f:
        json.dump(result, f, ensure_ascii=False, indent=2, allow_nan=False)


if __name__ == '__main__':
    main()
