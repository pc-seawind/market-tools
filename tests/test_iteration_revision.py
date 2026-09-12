"""Replay retained real inputs ONLY as test fixture; clock/collector boundaries only; engines and gates remain real."""
import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest
from mt1 import iteration_loop as L
from mt1 import action_loop as A
from mt1.timing_cli import file_hash

SOURCE=Path(__file__).resolve().parents[1]/'reports/mt14-20260913/real-release/runs/2026-09-12-966e72cc301b962a/inputs.json'

def replay_inputs(source,tmp_path):
    inputs=L.read(source)['result']
    # Small, explicitly derived test sweep. Source rows are retained real bytes;
    # this is replay, NOT a new collection or real forward sample.
    from mt1.candidates import screen
    from mt1.iteration_evidence import fundamental
    from mt1.store import digest as source_digest
    code=next(x['code'] for x in screen(inputs['fundamental']['snapshot'])['candidates'] if x['metrics']['roe']<12)
    observation=next(o for o in inputs['fundamental']['snapshot']['observations'] if o['stock']['ts_code']==code)
    sweep=tmp_path/'fixture-sweep';sweep.mkdir(parents=True);(sweep/'symbols').mkdir()
    at=inputs['fundamental']['asof'];day=inputs['fundamental']['snapshot']['asof']
    stock=[observation['stock']];daily=[observation['daily']];financial=observation['financials']
    for name,rows in [('stock_basic',stock),('daily_basic',daily)]:
        L.atomic_json(sweep/(name+'.json'),rows)
        L.atomic_json(sweep/(name+'-source.json'),{'fetched_at':at,'hash':source_digest(rows),'fresh_api':True})
    L.atomic_json(sweep/('financial-'+code+'.json'),financial)
    L.atomic_json(sweep/'symbols'/(code+'.json'),{'asof':day,'code':code,'fetched_at':at,'data_hash':source_digest(financial)})
    L.atomic_json(sweep/'summary.json',{'asof':day,'updated_at':at,'classification':'current_snapshot_shadow_not_historical_PIT','fresh_api':True,'universe_hash':source_digest(stock)})
    inputs['fundamental']=fundamental(sweep,tmp_path/'fixture-sources',at)
    return inputs


def replay_clock(monkeypatch,at):
    # Both modules hold their own now binding. Freeze ONLY the clock boundary:
    # never rewrite bundle/source/asof timestamps or bypass observe/validate/publish.
    monkeypatch.setattr(A,'now',lambda:at)
    monkeypatch.setattr(L,'now',lambda:at)


@pytest.fixture
def replay(tmp_path,monkeypatch):
    inputs=replay_inputs(SOURCE,tmp_path)
    replay_clock(monkeypatch,inputs['asof'])
    monkeypatch.setattr(L,'collect_inputs',lambda *args:copy.deepcopy(inputs))
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


def test_replay_after_far_future_wall_clock(replay,monkeypatch):
    class FutureWallClock(datetime):
        @classmethod
        def now(cls,tz=None):
            return datetime(2040,1,1,tzinfo=timezone.utc).astimezone(tz)
    monkeypatch.setattr(A,'datetime',FutureWallClock)
    assert A.datetime.now(timezone.utc).year==2040
    assert A.now()==L.read(SOURCE)['result']['asof']
    original_hash=file_hash(SOURCE)
    root,kw=replay
    assert L.run(root,**kw)['engineering_status']=='completed'
    assert not L.verify(root)['incomplete']
    assert file_hash(SOURCE)==original_hash


@pytest.mark.parametrize('age_seconds',[1800,1801])
def test_original_real_engine_age_gate(tmp_path,monkeypatch,age_seconds):
    inputs=L.read(SOURCE)['result'];bundle=inputs['technical']['bundle']
    assert bundle['source_kind']=='real_current_readonly_collection'
    at=(L.instant_time(bundle['asof'])+timedelta(seconds=age_seconds)).isoformat()
    replay_clock(monkeypatch,at)
    bp=tmp_path/'unaltered-real-bundle.json';L.atomic_json(bp,bundle)
    before=file_hash(bp)
    if age_seconds==1801:
        with pytest.raises(ValueError,match='live_bundle_stale_recollect_no_historic_order_backfill'):
            A.observe(bp,inputs['scope_path'],tmp_path/'ledger',execution_model='observed-quote-v1')
        assert not A.snapshots(tmp_path/'ledger')
    else:
        result=A.observe(bp,inputs['scope_path'],tmp_path/'ledger',execution_model='observed-quote-v1')
        assert result['manifest']
    assert file_hash(bp)==before


def test_stale_real_run_same_request_recollects_and_recovers(tmp_path,monkeypatch):
    # Two genuinely distinct retained collections, not re-dated old provider bytes.
    # Only the collector boundary is replayed; this test makes no network/fill claim.
    fresh_source=SOURCE.parents[4]/'mt14-r2/real-release/runs/mt14-r2-re-6ed9027cc3f86deb/inputs.json'
    old=replay_inputs(SOURCE,tmp_path/'old')
    fresh=replay_inputs(fresh_source,tmp_path/'fresh')
    at=(L.instant_time(fresh['asof'])+timedelta(minutes=10)).isoformat()
    assert (L.instant_time(at)-L.instant_time(old['technical']['bundle']['asof'])).total_seconds()>1800
    assert (L.instant_time(at)-L.instant_time(fresh['technical']['bundle']['asof'])).total_seconds()<1800
    assert old['source_scope_hash']==fresh['source_scope_hash']
    replay_clock(monkeypatch,at);attempts=[]
    def collect(root,d,*args):
        attempts.append(d)
        return copy.deepcopy(old if len(attempts)==1 else fresh)
    monkeypatch.setattr(L,'collect_inputs',collect)
    root=tmp_path/'recovery';kw=dict(sweep=None,scope=old['scope_path'],request_id='same-request')
    with pytest.raises(ValueError,match='live_bundle_stale_recollect_no_historic_order_backfill'):
        L.run(root,**kw)
    failed=attempts[0]
    assert not (failed/'manifest.json').exists()
    before={str(p):file_hash(p) for p in failed.rglob('*') if p.is_file()}
    assert L.read(failed/'failure.json')['engineering_status']=='incomplete'
    result=L.run(root,**kw)
    assert result['engineering_status']=='completed'
    assert len(attempts)==2 and attempts[0]!=attempts[1]
    assert L.read(failed/'recovery.json')['superseded_by']==str(attempts[1])
    assert all(file_hash(p)==h for p,h in before.items())
    assert L.read(attempts[1]/'inputs.json')['result']['technical']==fresh['technical']
    assert not L.verify(root)['incomplete']
    assert L.run(root,**kw)['idempotent'] and len(attempts)==2
