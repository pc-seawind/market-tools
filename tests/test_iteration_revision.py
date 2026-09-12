"""Replay retained real inputs ONLY as test fixture; no provider/verify/release mocks."""
import copy
from pathlib import Path
import pytest
from mt1 import iteration_loop as L
from mt1 import iteration_release as R
from mt1.timing_cli import file_hash

SOURCE=Path(__file__).resolve().parents[1]/'reports/mt14-20260913/real-release/runs/2026-09-12-966e72cc301b962a/inputs.json'

@pytest.fixture
def replay(tmp_path,monkeypatch):
    inputs=L.read(SOURCE)['result']
    # Small, explicitly derived test sweep. Source rows are retained real bytes;
    # this is replay, NOT a new collection or real forward sample.
    from mt1.candidates import screen
    from mt1.iteration_evidence import fundamental
    from mt1.store import digest as source_digest
    code=next(x['code'] for x in screen(inputs['fundamental']['snapshot'])['candidates'] if x['metrics']['roe']<12)
    observation=next(o for o in inputs['fundamental']['snapshot']['observations'] if o['stock']['ts_code']==code)
    sweep=tmp_path/'fixture-sweep';sweep.mkdir();(sweep/'symbols').mkdir()
    at=inputs['fundamental']['asof'];day=inputs['fundamental']['snapshot']['asof']
    stock=[observation['stock']];daily=[observation['daily']];financial=observation['financials']
    for name,rows in [('stock_basic',stock),('daily_basic',daily)]:
        L.atomic_json(sweep/(name+'.json'),rows)
        L.atomic_json(sweep/(name+'-source.json'),{'fetched_at':at,'hash':source_digest(rows),'fresh_api':True})
    L.atomic_json(sweep/('financial-'+code+'.json'),financial)
    L.atomic_json(sweep/'symbols'/(code+'.json'),{'asof':day,'code':code,'fetched_at':at,'data_hash':source_digest(financial)})
    L.atomic_json(sweep/'summary.json',{'asof':day,'updated_at':at,'classification':'current_snapshot_shadow_not_historical_PIT','fresh_api':True,'universe_hash':source_digest(stock)})
    inputs['fundamental']=fundamental(sweep,tmp_path/'fixture-sources',at)
    def collect(root,d,*args):
        return copy.deepcopy(inputs)
    monkeypatch.setattr(L,'collect_inputs',collect)
    return tmp_path/'replay',dict(sweep=None,scope=inputs['scope_path'],request_id='fixture-only')

@pytest.mark.parametrize('point',['after_manifest','before_release_evidence','after_release_evidence','after_release_fundamental','after_release_technical'])
def test_full_run_same_command_recovery(replay,point):
    root,kw=replay
    with pytest.raises(SystemExit):L.run(root,fail_at=point,**kw)
    assert L.verify(root)['incomplete']
    assert L.run(root,**kw)['engineering_status']=='completed'
    assert not L.verify(root)['incomplete']
    assert len(list((root/'runs').iterdir()))==1
    before={str(p):file_hash(p) for p in root.rglob('*') if p.is_file()}
    assert L.run(root,**kw)['idempotent']
    assert before=={str(p):file_hash(p) for p in root.rglob('*') if p.is_file()}
    assert len([p for p in (root/'events').glob('*') if L.read(p)['key'].startswith('release:')])==2

@pytest.mark.parametrize('mutation',['production','category','kind','unknown','identity','incomplete_intent','completion'])
def test_completed_real_fixture_readonly_tampering(replay,mutation):
    root,kw=replay;L.run(root,**kw);assert not L.verify(root)['incomplete']
    p=root/'releases/fundamental/active.json'
    forged={'version':'FORGED','production':False,'category':'fundamental','kind':'REAL_CURRENT'}
    if mutation=='identity':p=root/'.mt14.json';forged={**L.read(p),'production':True}
    elif mutation=='completion':p=next((root/'release-stages').glob('*/complete.json'));forged={}
    elif mutation=='incomplete_intent':p=root/'releases/fundamental/intents/forged.json'
    else:
        if mutation in ('production','category','kind'):forged[mutation]={'production':True,'category':'technical','kind':'SYNTHETIC_ONLY'}[mutation]
    L.atomic_json(p,forged)
    before={str(p):file_hash(p) for p in root.rglob('*') if p.is_file()}
    with pytest.raises((ValueError,KeyError,OSError)):L.verify(root)
    assert before=={str(p):file_hash(p) for p in root.rglob('*') if p.is_file()}


def test_empty_root_not_closed_loop(tmp_path):
    r=L.init(tmp_path/'empty','REAL_CURRENT');assert not L.verify(r)['closed_loop_executed']
