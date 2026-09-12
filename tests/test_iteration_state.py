import pytest
from mt1.iteration_loop import init, child, locked, stage, event

def test_isolation_and_stage_resume(tmp_path):
    r=init(tmp_path/'real','REAL_CURRENT')
    with pytest.raises(ValueError): init(r,'SYNTHETIC_ONLY')
    with pytest.raises(ValueError): child(r,'../production.json')
    d=r/'runs/a'; calls=[]
    assert stage(r,d,'input',lambda:calls.append(1) or {'x':1})=={'x':1}
    assert stage(r,d,'input',lambda:1/0)=={'x':1}
    assert calls==[1]
    with locked(r):
        with pytest.raises(BlockingIOError):
            with locked(r):pass
    event(r,'k',{'a':1})
    with pytest.raises(ValueError):event(r,'k',{'a':2})

def test_refuse_nonempty_and_symlink(tmp_path):
    (tmp_path/'prod').mkdir();(tmp_path/'prod/a').write_text('keep')
    with pytest.raises(ValueError):init(tmp_path/'prod','REAL_CURRENT')
    r=init(tmp_path/'new','SYNTHETIC_ONLY');(r/'escape').symlink_to(tmp_path/'prod',target_is_directory=True)
    with pytest.raises(ValueError):child(r,'escape/a')
