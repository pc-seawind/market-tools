"""Explicit SYNTHETIC_ONLY forward exercise, never real stock/return evidence."""
import copy
from datetime import date,timedelta
from pathlib import Path
from .action_loop import write,observe,weekly,snapshots,POLICY,read
from .timing import digest


def fixture():
    end=date(2026,9,14);dates=[];d=end
    while len(dates)<120:
        if d.weekday()<5:dates.append(str(d))
        d-=timedelta(days=1)
    rows=[]
    for i,d in enumerate(reversed(dates)):
        c=100+i*.05;w=.3 if i>=115 else 1
        rows.append({'date':d,'open':c-.1,'high':c+w,'low':c-w,'close':c,'vol':100,
                     'factor':1,'close_at':d+'T17:00:00+08:00'})
    p={'code':'SYNTH','market':'CN','bars':rows,'sessions':[b['date'] for b in rows],
       'expected_date':rows[-1]['date'],'calendar_verified':True,'calendar_coverage_through':rows[-1]['date'],
       'adjustment':'vendor_factor_verified','basis_id':'SYNTHETIC_CONSTANT_UNIT','benchmarks':{},
       'fetched_at':rows[-1]['date']+'T18:00:00+08:00','confidence':0.01}
    return p


def append(p, close, vol=100, opening=None):
    p=copy.deepcopy(p);d=date.fromisoformat(p['expected_date'])+timedelta(days=1)
    while d.weekday()>4:d+=timedelta(days=1)
    d=str(d);opening=close-.1 if opening is None else opening
    p['bars'].append({'date':d,'open':opening,'high':max(opening,close)+.3,'low':min(opening,close)-.3,
                      'close':close,'vol':vol,'factor':1,'close_at':d+'T17:00:00+08:00'})
    p['sessions'].append(d);p.update(expected_date=d,calendar_coverage_through=d,fetched_at=d+'T18:00:00+08:00')
    return p


def make_scope(path,held=False):
    item={'code':'SYNTH','name':'合成样本','market':'CN','admitted_at':'2026-09-12T01:00:00+08:00',
          'research_event_id':'SYNTHETIC_ADMISSION'}
    write(path,{'epoch':'SYNTHETIC-epoch','reset_at':'2026-09-12T00:00:00+08:00',
                'holdings':[item] if held else [],'active_candidates':[] if held else [item]})


def bundle(p,root,number,quote=False):
    d=p['expected_date'];asof=p['fetched_at'];root=Path(root)
    q={'code':p['code'],'market':p['market'],'date':d,'open_at':d+'T09:30:00+08:00',
       'observed_at':d+'T09:30:05+08:00','eligibility_known_at':d+'T09:29:59+08:00',
       'session_verified':True,'halted':False,'settlement_ok':True,'limit_up':False,'limit_down':False,
       'vcm_clear':True,'open':p['bars'][-1]['open'],'factor':1,'basis_id':p['basis_id']}
    raw=root/f'source-{number}.json';write(raw,{'synthetic':True,'panel':p,'open_snapshot':q if quote else None})
    from .timing_cli import file_hash
    h=file_hash(raw)
    inputs=[{'path':str(raw.resolve()),'sha256':h,'fetched_at':asof}]
    if quote:
        qp=root/f'open-{number}.json';write(qp,q);qh=file_hash(qp)
        inputs.append({'path':str(qp.resolve()),'sha256':qh,'fetched_at':q['observed_at']})
        q['source_sha256']=qh
    b={'source_kind':'SYNTHETIC_ONLY','asof':asof,'scope_epoch':'SYNTHETIC-epoch','scope_codes':[p['code']],
       'panels':[p],'inputs':inputs,
       'contract_hash':digest(read(POLICY)['timing']),'execution_quotes':[q] if quote else []}
    path=root/f'bundle-{number}.json';write(path,b);return path


def demo(out):
    root=Path(out);root.mkdir(parents=True,exist_ok=False);scope=root/'scope.json';make_scope(scope)
    p=fixture();receipts=[];actions=[]
    for i,(close,vol,quote,opening) in enumerate([(None,100,False,None),(108,200,False,None),
                                                (109,100,True,108.5),(80,100,False,None),(80,100,True,79)]):
        if close is not None:p=append(p,close,vol,opening)
        receipt=observe(bundle(p,root,i,quote),scope,root/'archive');receipts.append(receipt)
        actions.append(snapshots(root/'archive')[-1][1]['cards'][0]['action'])
    s=snapshots(root/'archive')[-1][1]
    assert len(s['closed'])==1 and not s['positions'], 'SYNTHETIC round_trip incomplete'
    report=weekly(root/'archive',p['fetched_at']);write(root/'weekly.json',report)
    result={'SYNTHETIC_ONLY':True,'actions':actions,'receipts':receipts,'round_trips':len(s['closed']),
            'fills':[o.get('fill') for o in s['ledger'].values() if o.get('fill')],
            'weekly':str(root/'weekly.json'),'not_real_trade_or_efficacy':True}
    write(root/'summary.json',result);return result
