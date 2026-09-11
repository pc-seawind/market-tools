"""Synthetic tests only; never read production scope or market services."""
import json
from datetime import datetime
from unittest.mock import patch
import pytest
from mt1.scope import load, filter_plans
from mt1.daily_tracking import track, calendars, dated_bars
from mt1.store import Store


@pytest.fixture
def scoped(tmp_path, monkeypatch):
    path=tmp_path/'scope.json'
    path.write_text(json.dumps({'scope_epoch':'reset-test','reset_at':'2026-09-11T10:00:00+00:00',
        'confirmed_holdings':[{'code':f'{n:06}.SZ'} for n in range(9)], 'active_candidates':[]}))
    monkeypatch.setenv('MT1_TRACKING_SCOPE',str(path))
    return path


def test_scope_nine_zero_no_legacy(scoped):
    plans=[{'code':f'{n:06}.SZ','state':'EXIT','holding_status':'confirmed','actual_cost':123} for n in range(64)]
    actual=filter_plans(plans)
    assert len(actual)==9
    assert all(p['state']=='EXIT' and p['actual_cost'] is None for p in actual)
    assert all(p['holding_status']=='confirmed' for p in actual)
    assert plans[0]['actual_cost']==123
    assert len(filter_plans([{'code':str(n)} for n in range(4714)]))==0


@pytest.mark.parametrize('content',[None,'{}','not-json'])
def test_invalid_fail_closed(scoped, content):
    if content is None: scoped.unlink()
    else: scoped.write_text(content)
    with pytest.raises(ValueError, match='fail_closed'): filter_plans([{'code':'000000.SZ'}])


def test_no_scope_library_compat(monkeypatch):
    monkeypatch.delenv('MT1_TRACKING_SCOPE',raising=False)
    p=[{'code':'old'}]
    assert filter_plans(p)==p


def test_no_candidate_readmission_by_rerun(scoped):
    value=json.loads(scoped.read_text())
    value['active_candidates']=[{'code':'old','admitted_at':'2026-09-10T10:00:00+00:00','research_event_id':'old'}]
    scoped.write_text(json.dumps(value))
    with pytest.raises(ValueError): load()


def test_new_candidate_not_held(scoped):
    value=json.loads(scoped.read_text())
    value['active_candidates']=[{'code':'NEW','admitted_at':'2026-09-11T11:00:00+00:00','research_event_id':'new'}]
    scoped.write_text(json.dumps(value))
    assert filter_plans([{'code':'NEW','holding_status':'confirmed'}])==[]
    p=filter_plans([{'code':'NEW','holding_status':'confirmed','scope_epoch':'reset-test','research_event_id':'new'}])[0]
    assert p['holding_status']=='not_held'


def test_out_of_scope_plan_write_blocked(scoped,tmp_path):
    store=Store(tmp_path/'test.db')
    try:
        with pytest.raises(ValueError,match='outside_tracking_scope'):
            store.apply('plan:old',0,'req',{'code':'old'},'test',lambda a,b:b)
        assert store.all()==[]
    finally: store.close()


def test_quotes_without_buy_or_cost_partial(scoped):
    def fetch(code):
        if code=='000003.SZ':raise ValueError('provider failed')
        return [{'date':'20260910','close':10},{'date':'20260911','close':11}]
    r=track(str(scoped),fetch=fetch,gates={'CN':{'expected_date':'2026-09-11','allowed':True}})
    assert len(r['rows'])==9 and len(r['failed'])==1 and r['status']=='partial'
    assert all(p['actual_cost'] is None and p['recommendation_return'] is None for p in r['rows'])
    assert r['rows'][0]['close']==11


def test_three_markets_independent():
    now=datetime.fromisoformat('2026-09-11T12:00:00+00:00')
    with patch('mt1.daily_tracking.cn_calendar',side_effect=ValueError('CN down')), \
         patch('mt1.daily_tracking.foreign_calendar',side_effect=lambda n,m: {'market':m}), \
         patch('mt1.daily_tracking.gate',side_effect=lambda s,m,p,n: {'allowed':True,'market':m,'expected_date':'2026-09-10'}):
        r=calendars(now)
    assert not r['CN']['allowed'] and r['HK']['allowed'] and r['US']['allowed']


def test_no_stale_substitution():
    with pytest.raises(ValueError,match='stale'):
        dated_bars([{'date':'20260910','close':10}], '2026-09-11')


def test_narrative_no_old_date():
    import narrative_track as nt
    with patch.object(nt,'_ts_csv',return_value=[]):
        assert nt._fetch_benchmark_close('A','20260911')[1] is None


def test_deploy_daily_preserves_policy():
    from mt1_deploy_cron import prompt
    from scripts.deploy_mt11_sidecar import section
    for phase in ('morning','evening'):
        assert 'daily-report-policy.md' in prompt(phase)
        assert 'tracking-scope.json' in prompt(phase)
        assert '第一屏市场摘要' in prompt(phase)
        assert '日报第一屏必须三地市场摘要' in section(phase)


@pytest.mark.parametrize('fail_count',[1,2,3,4])
def test_enrich_no_evening_partial_manifest(scoped,tmp_path,monkeypatch,fail_count):
    import sys, yaml
    import thesis_enrich_daily as ted
    thesis=tmp_path/'thesis';thesis.mkdir()
    for n in range(9):
        (thesis/f'{n:06}.SZ.yaml').write_text(yaml.safe_dump({'ticker':f'{n:06}.SZ','status':'ACTIVE','pillars':[{'name':'growth'}]}))
    before={p:p.read_bytes() for p in thesis.glob('*')}
    out=tmp_path/'manifest.json'
    monkeypatch.setattr(sys,'argv',['enrich','--scope',str(scoped),'--thesis-dir',str(thesis),'--date','2026-09-11','--dry-run','--out',str(out)])
    monkeypatch.setattr('mt1.daily_tracking.calendars',lambda now:{'CN':{'allowed':True,'expected_date':'2026-09-11'}})
    def fetch(code):
        if int(code[:6])<fail_count:raise ValueError('fixture provider failure')
        return [{'date':'20260911','close':10}]
    monkeypatch.setattr(ted,'fetch_daily_bars',fetch)
    from test_thesis_enrich_daily import sample_metrics
    monkeypatch.setattr(ted,'calc_technical_metrics',lambda b:sample_metrics())
    monkeypatch.setattr(ted,'detect_signal',lambda b:[])
    with pytest.raises(SystemExit) as error:ted.main()
    assert error.value.code==75
    result=json.loads(out.read_text())
    assert len(result['failed'])==fail_count and result['updated']==9-fail_count
    assert result['silent'] is False
    assert all(p.read_bytes()==raw for p,raw in before.items())


def test_pillars_pending_no_default_stop():
    from thesis_enrich_daily import build_update_entry,check_stop_loss
    from test_thesis_enrich_daily import sample_metrics
    thesis={'pillars':[{'name':'growth'}], 'stop_loss':{'price_trigger':{'level':'-15%'},'thesis_trigger':['decline']}}
    status,triggers=check_stop_loss(thesis,sample_metrics())
    assert triggers==[] and '框架性检查通过' not in status
    assert build_update_entry('2026-09-11',thesis,sample_metrics(),[],None,None,status,'test')['pillar_impact']=={'growth':'PENDING'}


def test_narrative_reject_wrong_provider_date():
    import narrative_track as nt
    with patch.object(nt,'_ts_csv',return_value=[{'trade_date':'20260910','close':'10'}]):
        assert nt._fetch_benchmark_close('HK','20260911')[1] is None


def test_migration_after_reset_no_new_legacy_plans(scoped,tmp_path):
    from mt1.pipeline import migrate
    rec=tmp_path/'recs.jsonl';rec.write_text(json.dumps({'code':'000000.SZ','action':'WATCH'})+'\n')
    thesis=tmp_path/'thesis';thesis.mkdir()
    s=Store(tmp_path/'history.db')
    try:
        assert migrate(s,rec,thesis)['imported']==0
        assert s.all()==[]
        assert len(s.all('legacy:rec:'))==1
    finally:s.close()
