import pytest
from mt1.iteration_loop import demo, stage, init, read


def test_full_demo_and_duplicate(tmp_path):
    r=demo(tmp_path/'demo')
    assert r['activation']['decision']=='experimental_activate'
    assert r['execution_roundtrip']['round_trips']==1
    assert len(r['paired_technical_versions'])==2
    assert r['real_sample_count']==0
    again=demo(tmp_path/'demo')
    assert again['idempotent']


def test_archive_failure_then_resume_stage(tmp_path):
    r=init(tmp_path/'r','SYNTHETIC_ONLY'); d=r/'runs/test'
    calls=[]
    def fail():calls.append(1);raise OSError('archive unavailable')
    with pytest.raises(OSError):stage(r,d,'a',fail)
    assert not (d/'a.json').exists()
    assert stage(r,d,'a',lambda:{'recovered':True})=={'recovered':True}
    assert stage(r,d,'a',fail)=={'recovered':True}
    assert calls==[1]
