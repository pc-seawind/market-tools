import json
import pytest
from mt1.iteration_loop import init, child
from mt1.iteration_release import publish, recover
from test_iteration_validate import frames


def test_actual_switch_rollback_and_recovery(tmp_path):
    r=init(tmp_path/'synthetic','SYNTHETIC_ONLY'); p=r/'evidence.json';p.write_text(json.dumps(frames()))
    args=(r,p,'fundamental','old','new','2026-09-13T00:00:00+00:00')
    with pytest.raises(RuntimeError):publish(*args,fail_at='after_prepare')
    assert not (r/'releases/fundamental/active.json').exists()
    assert recover(r)[0]['action']=='resume_prepared'
    assert publish(*args)['idempotent']
    p.write_text('[]')
    assert recover(r)[0]['action']=='automatic_rollback'
    assert json.loads((r/'releases/fundamental/active.json').read_text())['version']=='old'

def test_reject_forged_caller_pass(tmp_path):
    r=init(tmp_path/'synthetic','SYNTHETIC_ONLY');p=r/'evidence.json';p.write_text('[]')
    assert publish(r,p,'fundamental','old','new','2026-09-13T00:00:00+00:00')['decision']=='continue_shadow'
    assert not (r/'releases/fundamental/active.json').exists()

def test_new_risk_evidence_rolls_back_not_only_hash_corruption(tmp_path):
    r=init(tmp_path/'synthetic','SYNTHETIC_ONLY');p=r/'pass.json';p.write_text(json.dumps(frames()))
    publish(r,p,'fundamental','old','new','2026-09-13T00:00:00+00:00')
    q=r/'reject.json';q.write_text(json.dumps(frames(True)))
    assert publish(r,q,'fundamental','old','new','2026-09-13T00:00:00+00:00')['decision']=='reject'
    assert json.loads((r/'releases/fundamental/active.json').read_text())['version']=='old'

def test_recover_prepare_over_existing_active_pointer(tmp_path):
    r=init(tmp_path/'r','SYNTHETIC_ONLY');p=r/'v1.json';p.write_text(json.dumps(frames()))
    publish(r,p,'fundamental','old','new','2026-09-13T00:00:00+00:00')
    f=frames()
    for x in f:x['candidate']='new2'
    q=r/'v2.json';q.write_text(json.dumps(f))
    with pytest.raises(RuntimeError):publish(r,q,'fundamental','old','new2','2026-09-14T00:00:00+00:00',fail_at='after_prepare')
    assert recover(r)[0]['action']=='resume_prepared'
    assert json.loads((r/'releases/fundamental/active.json').read_text())['version']=='new2'
    assert not recover(r)

def test_real_release_binds_historical_prefix_not_later_frames(tmp_path,monkeypatch):
    from mt1 import iteration_loop as loop
    from mt1.iteration_release import recompute
    r=init(tmp_path/'r','REAL_CURRENT');f=frames()
    for x in f:x['kind']='REAL_CURRENT'
    p=r/'evidence.json';p.write_text(json.dumps(f))
    later={**f[-1],'observed_at':'2026-09-14T00:00:00+00:00'}
    monkeypatch.setattr(loop,'load_frames',lambda *a,**kw:f+[later])
    assert recompute(r,p,'fundamental','old','new','2026-09-13T00:00:00+00:00')['decision']=='experimental_activate'

@pytest.mark.parametrize('point',['after_prepare','after_switch'])
def test_activation_crash_recovery_readonly_chain(tmp_path,point):
    from mt1.iteration_release import verify_releases
    r=init(tmp_path/'r','SYNTHETIC_ONLY');p=r/'evidence.json';f=frames()
    for v in f:v['baseline']='qv-shadow-1'
    p.write_text(json.dumps(f))
    args=(r,p,'fundamental','qv-shadow-1','new','2026-09-13T00:00:00+00:00')
    with pytest.raises(RuntimeError):publish(*args,fail_at=point)
    with pytest.raises(ValueError,match='uncommitted'):verify_releases(r)
    assert len(recover(r))==1
    assert publish(*args)['idempotent']
    assert verify_releases(r)['active_categories']==['fundamental']
    assert len(list((r/'releases/fundamental/intents').glob('*.committed')))==1

@pytest.mark.parametrize('mutation',['evidence_hash','evidence_missing','previous','commit','intent','active_missing'])
def test_committed_chain_tampering_is_readonly(tmp_path,mutation):
    from mt1.iteration_release import verify_releases
    from mt1.iteration_loop import atomic_json,read
    from mt1.timing_cli import file_hash
    r=init(tmp_path/'r','SYNTHETIC_ONLY');p=r/'evidence.json';f=frames()
    for v in f:v['baseline']='qv-shadow-1'
    p.write_text(json.dumps(f));publish(r,p,'fundamental','qv-shadow-1','new','2026-09-13T00:00:00+00:00')
    target=r/'releases/fundamental/active.json';active=read(target)
    if mutation=='evidence_hash':p.write_text('[]')
    elif mutation=='evidence_missing':p.rename(r/'retained-evidence.json')
    elif mutation=='previous':active['previous']['version']='FORGED';atomic_json(target,active)
    elif mutation=='commit':atomic_json(next((r/'releases/fundamental/intents').glob('*.committed')),{'pointer_hash':'FORGED'})
    elif mutation=='intent':atomic_json(next((r/'releases/fundamental/intents').glob('*.json')),{'production':True})
    elif mutation=='active_missing':target.rename(target.with_suffix('.retained'))
    before={str(x):file_hash(x) for x in r.rglob('*') if x.is_file()}
    with pytest.raises((ValueError,KeyError,OSError)):verify_releases(r)
    assert before=={str(x):file_hash(x) for x in r.rglob('*') if x.is_file()}
