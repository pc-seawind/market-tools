from datetime import datetime, timedelta, timezone
import copy
import pytest
from mt1.iteration_validate import validate
from mt1.iteration_policy import CONTRACT
from mt1.timing import digest


def frames(rising=False):
    result=[]; d=datetime(2025,1,1,tzinfo=timezone.utc); dates=[]
    while len(dates)<62:
        if d.weekday()<5: dates.append(d.date().isoformat())
        d+=timedelta(days=1)
    for i,day in enumerate(dates):
        result.append({'kind':'SYNTHETIC_ONLY','category':'fundamental','baseline':'old','candidate':'new',
            'contract_hash':digest(CONTRACT),'observed_at':day+'T18:00:00+00:00',
            'scope_codes':[f'S{n}' for n in range(20)],'capital_per_code':CONTRACT['capital_per_code'],
            'cost_bps':CONTRACT['per_side_cost_bps'],
            'rows':[{'code':f'S{n}','date':day,'close_at':day+'T15:00:00+00:00',
                     'previous_session':dates[i-1] if i else None,'market':'CN','basis':'SYNTHETIC',
                     'price':100+i*(.5 if rising else -.5), 'baseline_selected':True,'candidate_selected':False} for n in range(20)]})
    return result


def go(f):return validate(f,'fundamental','old','new',kind='SYNTHETIC_ONLY',asof='2026-09-13T00:00:00+00:00')

def test_recompute_not_claims_and_nonoverlap():
    f=frames();r=go(f)
    assert r['decision']=='experimental_activate' and r['independent_events']==20
    assert go(f+f)['independent_events']==20
    assert go(frames(True))['decision']=='reject'
    f=f[:20]
    for x in f:x.update(oos_pass=True,sample_count=99999)
    assert go(f)['decision']=='continue_shadow'

@pytest.mark.parametrize('mutation', ['future','kind','cost','revision','gap'])
def test_bad_evidence(mutation):
    f=frames()
    if mutation=='future':f[0]['observed_at']='2030-01-01T00:00:00+00:00'
    if mutation=='kind':f[0]['kind']='REAL_CURRENT'
    if mutation=='cost':f[0]['capital_per_code']=1
    if mutation=='revision':
        f.append(copy.deepcopy(f[0]));f[-1]['rows'][0]['price']=999
    if mutation=='gap':
        f.pop(2)
        assert go(f)['decision']=='continue_shadow';return
    with pytest.raises(ValueError):go(f)
