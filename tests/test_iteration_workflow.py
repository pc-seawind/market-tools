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


def test_failed_run_same_command_recollects_without_overwriting(tmp_path,monkeypatch):
    from mt1 import iteration_loop as loop
    scope=tmp_path/'scope.json';scope.write_text('original')
    root=tmp_path/'real';attempts=[]
    def fail(root,d,*args):
        attempts.append(d);raise ValueError('actual_provider_failed')
    monkeypatch.setattr(loop,'collect_inputs',fail)
    for _ in range(2):
        with pytest.raises(ValueError,match='actual_provider_failed'):
            loop.run(root,sweep=None,scope=scope,request_id='test')
    assert len(set(attempts))==2
    assert all((p/'failure.json').exists() for p in attempts)
    assert read(attempts[0]/'recovery.json')['superseded_by']==str(attempts[1])
    assert scope.read_text()=='original'


def test_synthetic_verify_not_only_self_asserted_hashes(tmp_path):
    import json
    from mt1.timing_cli import file_hash
    r=tmp_path/'demo';summary=demo(r)
    source=next((r/'raw').glob('*.json'));v=read(source)
    v['snapshot']['observations'][0]['financials'][0]['roe']=100
    source.write_text(json.dumps(v))
    summary['artifacts'][str(source)]=file_hash(source)
    (r/'demo-summary.json').write_text(json.dumps(summary))
    with pytest.raises(ValueError,match='synthetic_engine_recompute'):demo(r)

def test_failure_after_manifest_is_not_reported_as_idempotent_success(tmp_path,monkeypatch):
    from mt1 import iteration_loop as loop
    from mt1.timing import digest
    scope=tmp_path/'scope';scope.write_text('scope');root=loop.init(tmp_path/'r','REAL_CURRENT')
    d=root/'runs'/('test-'+digest('test')[:16]);d.mkdir(parents=True)
    loop.atomic_json(d/'manifest.json',{'summary':{'engineering_status':'completed'}})
    loop.atomic_json(d/'failure.json',{'engineering_status':'incomplete','error':'release_archive_failed'})
    calls=[]
    def fail(root,path,*args):calls.append(path);raise OSError('provider_failed')
    monkeypatch.setattr(loop,'collect_inputs',fail)
    with pytest.raises((ValueError,KeyError)):loop.run(root,sweep=None,scope=scope,request_id='test')
    assert not calls and not (d/'recovery.json').exists()
    assert loop.status(root)['runs'][0]['engineering_status']=='incomplete'
