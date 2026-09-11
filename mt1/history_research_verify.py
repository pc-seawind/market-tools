"""Read-only artifact audit, distinct from generating replay. Not acceptance."""
import json
import math
from collections import Counter,defaultdict
from pathlib import Path
from .history_research import sha


def verify(out):
    spec=json.loads((out/'frozen-contract.json').read_text())
    result=json.loads((out/'full-results.json').read_text())
    assert result['contract_sha256']==sha(out/'frozen-contract.json')
    raw_count=0
    for path in (out/'raw').glob('*.meta.json'):
        meta=json.loads(path.read_text());body=path.with_name(path.name.replace('.meta.json','.json'))
        assert sha(body)==meta['sha256'],str(path)
        assert 'token' not in meta['request']
        parsed=json.loads(body.read_bytes());assert parsed['code']==0
        assert len(parsed['data']['items'])==meta['rows'];raw_count+=1
    old=out.parent/'timing-upgrade-20260911'
    before=json.loads((out/'old-evidence-before.json').read_text())
    for name,proof in before.items():
        assert sha(old/name)==proof['expected']==proof['actual'],name
    panels={code:json.loads((out/f'panel-{code}.json').read_text()) for code in spec['codes']}
    assert set(panels)==set(spec['codes']) and not result['collection_failures']
    folds={(f['code'],f['fold']):f for f in result['folds']}
    assert len(folds)==len(result['folds'])
    scenario_copies=defaultdict(list);a_entries=defaultdict(list);closed=0
    for t in result['trades']:
        p=panels[t['code']];fold=folds[t['code'],t['fold']]
        assert fold['test_start']<=t['signal_date']<=fold['test_end']
        scenario_copies[(t['code'],t['fold'],t['group'],t['arm'],t['scenario'])].append(t)
        ent=t['entry_fill']
        if t['group']=='A':a_entries[t['episode_id'],t['scenario'],t['cost_bps']].append(ent)
        if ent['status']=='filled':
            assert ent['index']>t['signal_index']
            assert ent['date']<=fold['test_end']
            assert ent['price']==p['bars'][ent['index']]['open']
            assert ent['raw_price']==p['bars'][ent['index']]['raw']['open']
            assert ent['open_at']==p['bars'][ent['index']]['open_at']
            assert 'known_at' not in ent
        if t['status']=='closed':
            closed+=1;ex=t['exit_fill'];assert ex['index']>=ent['index']+1
            assert t['exit_signal_date']<ex['date']<=fold['test_end']
            assert ex['price']==p['bars'][ex['index']]['open']
            c=t['cost_bps']/10000
            independently=ex['price']*(1-c)/(ent['price']*(1+c))-1
            assert math.isclose(independently,t['net_price_return'],abs_tol=1e-12)
            for side,fill in [('buy',ent),('sell',ex)]:
                b=p['bars'][fill['index']];e=p['evidence'][fill['index']]
                assert not any(s['suspend_type']=='S' for s in e['suspension'])
                limit=e['limit']['up_limit' if side=='buy' else 'down_limit']
                assert b['raw']['open']<limit-.005 if side=='buy' else b['raw']['open']>limit+.005
        else:assert t['net_price_return'] is None
    for ts in scenario_copies.values():
        ts=sorted(ts,key=lambda t:t['cost_bps'])
        assert [t['cost_bps'] for t in ts]==spec['costs_bps_per_side']
        assert all(t['entry_fill']==ts[0]['entry_fill'] for t in ts)
        assert all(t.get('exit_fill')==ts[0].get('exit_fill') for t in ts)
        if ts[0]['status']=='closed':assert all(b['net_price_return']<a['net_price_return'] for a,b in zip(ts,ts[1:]))
    same_entry=0;different_entry=0
    for es in a_entries.values():
        assert len(es)==3
        if all(e==es[0] for e in es):same_entry+=1
        else:different_entry+=1
    assert closed>0 and raw_count>0 and len(before)==152
    return dict(status='developer_readonly_audit_not_independent_acceptance',old_files_verified=len(before),raw_responses_verified=raw_count,
                securities=len(panels),stock_oos_folds=len(folds),closed_rows_including_cost_scenario_duplicates=closed,
                A_episode_scenario_cost_sets_same_entry=same_entry,A_sets_cancelled_differentially=different_entry,
                baseline_unique_closed_by_arm={k:v['round_trips'] for k,v in result['summary'].items() if k.startswith('open_price_limit_base_v1|15|')})
