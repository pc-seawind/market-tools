"""All fixtures SYNTHETIC_ONLY. These assertions do NOT validate efficacy."""
import copy
from pathlib import Path
import pytest
from mt1.action_loop import (observe,snapshots,read,write,POLICY,weekly,decide,
                             executable,register_policy,cancel,daily_report)
from mt1.action_demo import fixture,append,bundle,make_scope,demo
from mt1.timing import digest


class Run:
    def __init__(self,tmp,held=False):
        self.tmp=tmp;self.root=tmp/'archive';self.scope=tmp/'scope.json'
        make_scope(self.scope,held);self.n=0
    def run(self,p,quote=False,edit=None,policy=POLICY):
        path=bundle(p,self.tmp,self.n,quote);self.n+=1
        if edit:
            b=read(path);edit(b);path.write_text(__import__('json').dumps(b))
        self.last_path=path
        self.receipt=observe(path,self.scope,self.root,policy)
        return snapshots(self.root)[-1][1]


def test_full_demo_real_separation(tmp_path):
    r=demo(tmp_path/'demo')
    assert r['actions']==['WAIT','BUY','HOLD','SELL','WAIT']
    assert r['round_trips']==1 and r['SYNTHETIC_ONLY']
    assert r['fills'][0]['raw_price']==108.5 and r['fills'][1]['raw_price']==79


def test_low_confidence_missing_rs_still_buy(tmp_path):
    h=Run(tmp_path);p=fixture();h.run(p);s=h.run(append(p,108,200))
    assert s['cards'][0]['action']=='BUY'
    assert s['cards'][0]['soft_annotations']['confidence']==.01
    assert len(s['ledger'])==1


def test_low_confidence_sell_risk_first_no_company_veto(tmp_path):
    h=Run(tmp_path,True);p=fixture();h.run(p);s=h.run(append(p,80))
    c=s['cards'][0];o=next(iter(s['ledger'].values()))
    assert c['action']=='SELL' and c['hard_gaps']==[]
    assert o['execution_status']=='blocked' and not s['positions']
    assert c['personal_pnl'] is None


def test_existing_holding_buy_is_add_observation(tmp_path):
    h=Run(tmp_path,True);p=fixture();h.run(p);s=h.run(append(p,108,200))
    assert '增持观察' in s['cards'][0]['applies_to']
    assert s['positions']=={}


def test_unheld_risk_never_short(tmp_path):
    h=Run(tmp_path);p=fixture();h.run(p);s=h.run(append(p,80))
    assert s['cards'][0]['action']=='WAIT'
    assert not s['ledger'] and not s['positions']


@pytest.mark.parametrize('mutation',['factor','calendar','price','latest','basis','future','history'])
def test_hard_data_blocks_not_confidence(tmp_path,mutation):
    h=Run(tmp_path,True);p=fixture();h.run(p);p=append(p,108,200)
    if mutation=='factor':p['bars'][-1]['factor']=None
    if mutation=='calendar':p['calendar_verified']=False
    if mutation=='price':p['bars'][-1]['high']=1
    if mutation=='latest':p['expected_date']='2000-01-01'
    if mutation=='basis':p['basis_id']='wrong'
    if mutation=='future':p['bars'][-1]['close_at']='2027-01-01T17:00:00+08:00'
    if mutation=='history':p['bars'][0]['factor']=2
    s=h.run(p);c=s['cards'][0]
    assert c['action']=='DATA_BLOCKED' and c['hard_gaps']
    assert all(g['field'] and g['reason'] and g['remedy_status'] for g in c['hard_gaps'])


def test_volume_missing_does_not_block_frozen_sell(tmp_path):
    h=Run(tmp_path,True);p=fixture();h.run(p);p=append(p,80);del p['bars'][-1]['vol']
    s=h.run(p)
    assert s['cards'][0]['action']=='SELL' and not s['cards'][0]['hard_gaps']
    # Restore full inputs on next date: same risk episode, no duplicate SELL.
    p['bars'][-1]['vol']=100;p=append(p,79);s=h.run(p)
    assert sum(o['side']=='SELL' for o in s['ledger'].values())==1


def test_missing_volume_without_known_breach_not_false_hold(tmp_path):
    h=Run(tmp_path,True);p=fixture();h.run(p);p=append(p,108);del p['bars'][-1]['vol']
    s=h.run(p);assert s['cards'][0]['action']=='DATA_BLOCKED'


def test_duplicate_run_no_duplicate_order(tmp_path):
    h=Run(tmp_path);p=fixture();h.run(p);s=h.run(append(p,108,200))
    receipt=observe(h.last_path,h.scope,h.root)
    assert receipt['idempotent'] and len(snapshots(h.root))==2
    assert len(s['ledger'])==1


def test_pending_cross_week_expiry_preserves_signal(tmp_path):
    h=Run(tmp_path);p=fixture();h.run(p);p=append(p,108,200);h.run(p)
    for _ in range(3):
        p=append(p,109,90);s=h.run(p)
    order=next(iter(s['ledger'].values()))
    assert order['side']=='BUY' and order['execution_status']=='expired'
    assert order['original_reasons']==['frozen_breakout_trigger']
    w=weekly(h.root,'2026-09-21T19:00:00+08:00')
    assert w['versions']['signal-policy-v1']['expired']==1


def test_week_boundary_retains_unexecuted_before_expiry(tmp_path):
    h=Run(tmp_path);p=fixture();h.run(p);h.run(append(p,108,200))
    w=weekly(h.root,'2026-09-21T19:00:00+08:00')
    assert w['versions']['signal-policy-v1']['cross_week_pending']


def test_risk_cancels_pending_buy_not_suppressed_signal(tmp_path):
    h=Run(tmp_path);p=fixture();h.run(p);p=append(p,108,200);h.run(p)
    s=h.run(append(p,80));o=next(iter(s['ledger'].values()))
    assert o['execution_status']=='cancelled' and o['side']=='BUY'
    assert s['cards'][0]['action']=='WAIT' and not s['positions']


def test_cancellation_idempotent_retains_original_reason(tmp_path):
    h=Run(tmp_path);p=fixture();h.run(p);s=h.run(append(p,108,200));o=next(iter(s['ledger'].values()))
    cancel(h.root,h.scope,o['signal_id'],'operator_simulation_cancel')
    assert cancel(h.root,h.scope,o['signal_id'],'operator_simulation_cancel')['idempotent']
    last=snapshots(h.root)[-1][1]['ledger'][o['signal_id']]
    assert last['original_reasons']==o['original_reasons']


def test_signal_emits_without_execution_info(tmp_path):
    h=Run(tmp_path);p=fixture();h.run(p);p=append(p,108,200);s=h.run(p)
    p=append(p,109);s=h.run(p)
    assert next(iter(s['ledger'].values()))['execution_status']=='pending'
    assert not s['positions']


def test_open_fill_before_same_days_close_risk(tmp_path):
    h=Run(tmp_path);p=fixture();h.run(p);p=append(p,108,200);h.run(p)
    s=h.run(append(p,80,opening=108.5),True)
    assert s['cards'][0]['action']=='SELL'
    assert len(s['positions'])==1
    orders=list(s['ledger'].values());assert orders[0]['execution_status']=='filled'
    assert orders[-1]['side']=='SELL' and orders[-1]['execution_status']=='pending'


def test_no_same_bar_or_stop_fill(tmp_path):
    h=Run(tmp_path);p=fixture();h.run(p);s=h.run(append(p,108,200),True)
    assert not s['positions']
    assert not next(iter(s['ledger'].values())).get('fill')


def execution_fixture(tmp_path,market='CN'):
    p=fixture();p['market']=market;bpath=bundle(p,tmp_path,0,True);b=read(bpath);q=b['execution_quotes'][0]
    order={'code':'SYNTH','market':market,'side':'BUY','signal_date':'2026-09-11',
           'triggered_at':'2026-09-11T18:00:00+08:00','basis_id':p['basis_id']}
    sources={s['sha256']:s for s in b['inputs']}
    return b,q,order,sources


def patch_quote(q,sources,key,value):
    q[key]=value
    # Explicit synthetic upstream snapshot, preserve hash verification in observe tests.
    path=Path(sources[q['source_sha256']]['path'])
    path.write_text(__import__('json').dumps({k:v for k,v in q.items() if k!='source_sha256'}))


@pytest.mark.parametrize('key,value,expected',[
    ('halted',True,'suspended'),('limit_up',True,'CN_side_limit'),
    ('settlement_ok',False,'settlement'),('observed_at','2026-09-14T18:00:00+08:00','noncontemporaneous'),
    ('eligibility_known_at','2026-09-14T10:00:00+08:00','eligibility'),
    ('basis_id','wrong','factor_basis')])
def test_execution_gate_only_not_signal(tmp_path,key,value,expected):
    b,q,o,src=execution_fixture(tmp_path);patch_quote(q,src,key,value)
    assert expected in executable(q,o,b['asof'],src)


def test_hk_does_not_use_cn_price_limit(tmp_path):
    b,q,o,src=execution_fixture(tmp_path,'HK');patch_quote(q,src,'limit_up',True)
    assert executable(q,o,b['asof'],src) is None
    patch_quote(q,src,'vcm_clear',False)
    assert 'HK_VCM' in executable(q,o,b['asof'],src)


def test_source_content_binding(tmp_path):
    b,q,o,src=execution_fixture(tmp_path);q['open']=12345
    assert 'source_content_mismatch' in executable(q,o,b['asof'],src)


def test_scope_reset_separate_root(tmp_path):
    h=Run(tmp_path);p=fixture();h.run(p)
    scope=read(h.scope);scope['epoch']='new';h.scope.write_text(__import__('json').dumps(scope))
    with pytest.raises(ValueError,match='scope_changed'):h.run(append(p,108,200))


def test_frozen_policy_mutation(tmp_path):
    h=Run(tmp_path);p=fixture();h.run(p);cfg=read(POLICY);cfg['reason']='changed'
    path=tmp_path/'mutant.json';write(path,cfg)
    with pytest.raises(ValueError,match='frozen_policy_mutation'):h.run(append(p,108,200),policy=path)


def test_versions_forward_only_and_separate(tmp_path):
    h=Run(tmp_path);p=fixture();h.run(p);cfg=read(POLICY)
    cfg.update(version='signal-policy-v2',parent='signal-policy-v1',reason='synthetic_only_experiment',effective_at='2026-09-15T00:00:00+08:00')
    path=tmp_path/'v2.json';write(path,cfg);register_policy(h.root,path)
    with pytest.raises(ValueError,match='not_effective'):h.run(p,policy=path)
    p=append(p,108,200);s=h.run(p);v1=list(s['ledger'])
    s=h.run(p,policy=path)
    assert list(s['ledger'])==v1 and len(s['states'])==2
    w=weekly(h.root,p['fetched_at']);assert set(w['versions'])=={'signal-policy-v1','signal-policy-v2'}
    cfg['version']='bad-backfill';cfg['effective_at']='2026-09-14T00:00:00+08:00';write(tmp_path/'bad.json',cfg)
    with pytest.raises(ValueError,match='start_forward'):register_policy(h.root,tmp_path/'bad.json')


def test_archive_tamper_detected(tmp_path):
    h=Run(tmp_path);h.run(fixture());path=Path(h.receipt['manifest']).parent/'result.json';path.write_text('{}')
    with pytest.raises(ValueError,match='archive_result_corrupt'):snapshots(h.root)


def test_source_tamper_detected(tmp_path):
    h=Run(tmp_path);p=fixture();path=bundle(p,tmp_path,0);b=read(path)
    Path(b['inputs'][0]['path']).write_text('corrupt')
    with pytest.raises(ValueError,match='source_bytes_changed'):observe(path,h.scope,h.root)


def test_out_of_order_rejected(tmp_path):
    h=Run(tmp_path);p=fixture();h.run(p);h.run(append(p,108,200));p['confidence']=0
    with pytest.raises(ValueError,match='out_of_order'):h.run(p)


def test_daily_research_signal_columns(tmp_path):
    h=Run(tmp_path,True);s=h.run(fixture());report=daily_report(s)
    assert '技术策略信号（模拟跟踪，非自动实盘）' in report
    assert '公司研究（独立）' in report and '前两段' in report
    assert 'SYNTH' in report and 'source_sha256' in report


def test_signal_horizons_continue_after_exit(tmp_path):
    from mt1.action_loop import mark_positions,signal_reviews
    p=fixture();order={'position_key':'v|SYNTH','basis_id':p['basis_id'],'signal_date':p['expected_date'],
                       'signal_adjusted_price':p['bars'][-1]['close'],'side':'SELL','signal_id':'test'}
    s={'ledger':{'test':order},'positions':{},'closed':[]}
    for _ in range(60):p=append(p,108)
    mark_positions(s,p,'v');r=signal_reviews([order])[0]
    assert all(v['status']=='observed_price_only' for v in r['horizons'].values())
    assert r['not_personal_pnl']


def test_cn_tplus1_cannot_sell_same_day(tmp_path):
    from mt1.action_loop import execute
    b,q,o,src=execution_fixture(tmp_path);o.update(side='SELL',position_key='v|SYNTH',execution_status='pending',signal_id='x')
    s={'positions':{'v|SYNTH':{'entry_date':q['date']}},'transitions':[]}
    execute(s,o,[q],b['asof'],src,read(POLICY))
    assert o['execution_status']=='blocked' and o['execution_reason']=='CN_T_plus_1'


def test_sell_limit_down_blocks_only_execution(tmp_path):
    b,q,o,src=execution_fixture(tmp_path);o['side']='SELL';patch_quote(q,src,'limit_down',True)
    assert 'CN_side_limit_blocked'==executable(q,o,b['asof'],src)


def test_missing_volume_and_revised_price_not_exit_bypass(tmp_path):
    h=Run(tmp_path,True);p=fixture();h.run(p);p=append(p,80);del p['bars'][-1]['vol']
    p['bars'][0]['factor']=2
    s=h.run(p);assert s['cards'][0]['action']=='DATA_BLOCKED'


def test_wrong_market_identity_is_blocked(tmp_path):
    h=Run(tmp_path,True);p=fixture();h.run(p);p=append(p,80);p['market']='HK'
    s=h.run(p);assert s['cards'][0]['action']=='DATA_BLOCKED'


def test_legacy_root_refused(tmp_path):
    h=Run(tmp_path);h.root.mkdir();(h.root/'legacy.json').write_text('{}')
    with pytest.raises(ValueError,match='non_mt13_root'):h.run(fixture())


def test_open_time_not_any_intraday_quote(tmp_path):
    b,q,o,src=execution_fixture(tmp_path)
    patch_quote(q,src,'open_at','2026-09-14T10:00:00+08:00')
    assert 'not_market_open' in executable(q,o,b['asof'],src)


def test_sell_episode_does_not_repeat_after_expiry(tmp_path):
    h=Run(tmp_path,True);p=fixture();h.run(p)
    for _ in range(5):p=append(p,80);s=h.run(p)
    assert len(s['ledger'])==1
    assert next(iter(s['ledger'].values()))['execution_status']=='expired'
    assert s['cards'][0]['action']=='SELL'


def test_execution_bundle_merge_preserves_original(tmp_path):
    from mt1.action_loop import merge_execution
    from mt1.timing_cli import file_hash
    p=fixture();path=bundle(p,tmp_path,0,True);b=read(path);original=file_hash(path)
    e={'scope_epoch':b['scope_epoch'],'source_kind':b['source_kind'],'execution_quotes':b['execution_quotes'],'inputs':b['inputs']}
    ep=tmp_path/'exec.json';write(ep,e);merged=merge_execution(path,ep)
    assert file_hash(path)==original and read(merged)['execution_quotes']
    assert merge_execution(path,ep)==merged


def test_expired_buy_cannot_fill_from_late_snapshot(tmp_path):
    h=Run(tmp_path);p=fixture();h.run(p);p=append(p,108,200);h.run(p)
    for _ in range(3):p=append(p,109);h.run(p)
    p=append(p,109);s=h.run(p,True)
    assert not s['positions'] and not any(o.get('fill') for o in s['ledger'].values())


def test_version_changed_parameters_accepts_same_readonly_panel(tmp_path):
    h=Run(tmp_path);p=fixture();h.run(p);cfg=read(POLICY)
    cfg.update(version='signal-policy-v2',parent='signal-policy-v1',reason='synthetic ATR experiment',effective_at='2026-09-15T00:00:00+08:00')
    cfg['timing']['atr_multiple']=2.5;path=tmp_path/'v2.json';write(path,cfg);register_policy(h.root,path)
    s=h.run(append(p,108,200),policy=path)
    assert s['policies']['signal-policy-v1']['timing']['atr_multiple']==3
    assert s['policies']['signal-policy-v2']['timing']['atr_multiple']==2.5


def test_compose_preserves_three_source_sections_and_receipt(tmp_path):
    from mt1.action_report import compose
    h=Run(tmp_path);h.run(fixture())
    paths=[]
    for name,text in [('one','第一段原始行情\n'),('two','第二段观点\n'),('company','第三段公司研究原文\n')]:
        path=tmp_path/(name+'.md');path.write_text(text);paths.append(path)
    out=tmp_path/'daily.md';r=compose(*paths,h.receipt['manifest'],out)
    assert out.read_text().startswith('\n\n'.join(p.read_text() for p in paths))
    assert r['not_published'] and r['source_kind']=='SYNTHETIC_ONLY'
    assert compose(*paths,h.receipt['manifest'],out)==r


def test_weekly_publish_idempotent(tmp_path):
    from mt1.action_report import publish_weekly
    h=Run(tmp_path);p=fixture();h.run(p)
    out=tmp_path/'weekly.json';r=publish_weekly(h.root,p['fetched_at'],out)
    assert Path(r['markdown']).exists()
    assert publish_weekly(h.root,p['fetched_at'],out)==r


def test_execution_terminal_still_pending_price_review(tmp_path):
    demo(tmp_path/'demo');root=tmp_path/'demo'/'archive'
    s=snapshots(root)[-1][1]
    from mt1.longitudinal import manifests
    m=read(read(root/'latest.json')['manifest'])
    assert all(e['status']=='pending' for e in m['events'])
    assert all(e['horizons']['60']['status']=='not_matured' for e in m['events'])


def test_bad_calendar_cannot_expire_order(tmp_path):
    h=Run(tmp_path);p=fixture();h.run(p);p=append(p,108,200);h.run(p)
    for _ in range(4):p=append(p,109)
    p['calendar_verified']=False;s=h.run(p)
    assert next(iter(s['ledger'].values()))['execution_status']=='pending'


def test_simulated_exit_does_not_close_real_holding_episode(tmp_path):
    h=Run(tmp_path,True);p=fixture();h.run(p);p=append(p,108,200);h.run(p)
    p=append(p,109,opening=108.5);h.run(p,True)
    p=append(p,80);h.run(p)
    p=append(p,79,opening=79);h.run(p,True)
    p=append(p,78);s=h.run(p)
    assert not s['positions'] and len(s['closed'])==1
    assert s['cards'][0]['action']=='SELL'  # actual holding was never mutated
    assert sum(o['side']=='SELL' for o in s['ledger'].values())==1
