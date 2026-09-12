from mt1.candidates import screen
from mt1.iteration_loop import fundamental_engines, propose
from mt1.iteration_policy import BASE

def snapshot():
    return {'asof':'2026-09-11','universe_size':1,'data_version':'test','observations':[
        {'stock':{'ts_code':'SYNTH','name':'fixture'},
         'daily':{'trade_date':'20260911','pe_ttm':15,'pb':2,'turnover_rate':1},
         'financials':[{'ann_date':'20260801','end_date':'20260630','roe':11,'ocfps':1,'eps':1,'debt_to_assets':30}]}]}

def test_real_engine_parameters_and_default_unchanged():
    s=snapshot(); assert screen(s)==screen(s,BASE['fundamental'])
    assert len(screen(s)['candidates'])==1
    assert not screen(s,{'roe_min':12})['candidates']
    e={'snapshot':s,'scope_codes':['SYNTH']}
    c=propose(e,None,'2026-09-13T00:00:00+00:00')['candidates']
    result=fundamental_engines(e,c)
    assert result['selected_counts']=={'qv-shadow-1':1,c[0]['id']:0}
    assert len(c)<=2

def test_low_confidence_never_vetoes_real_engine_buy_sell():
    from mt1.action_demo import fixture, append
    from mt1.action_loop import decide, read, POLICY
    p=fixture();r,_=decide(p,p['fetched_at'],None,False,read(POLICY))
    p=append(p,108,200);p['confidence']=0
    r,c=decide(p,p['fetched_at'],r['state'],False,read(POLICY))
    assert c['action']=='BUY' and not c['soft_annotations']['confidence_is_veto']
    p=append(p,80,100);p['confidence']=0
    _,c=decide(p,p['fetched_at'],r['state'],True,read(POLICY))
    assert c['action']=='SELL'

def test_successor_uses_active_baseline_and_does_not_wait_for_other_category(tmp_path):
    from mt1.iteration_loop import init,select_generation,atomic_json
    from mt1.iteration_policy import candidate
    at='2026-09-13T00:00:00+00:00';r=init(tmp_path/'r','SYNTHETIC_ONLY')
    f=candidate('fundamental',{'roe_min':12},'prior','source',at)
    t=candidate('technical',{'breakout_volume_min':1.4},'prior','source',at)
    atomic_json(r/'candidates.json',{'candidates':[f,t],'keep_reasons':[]})
    atomic_json(r/'candidate-descriptors'/ (f['id']+'.json'),f)
    atomic_json(r/'releases/fundamental/active.json',{'version':f['id']})
    atomic_json(r/'runs/a/manifest.json',{})
    atomic_json(r/'runs/a/evaluation.json',{'result':[{'candidate':f['id'],'decision':'experimental_activate'}]})
    new=select_generation(r,{'asof':at})
    c=new['candidates'][0]
    assert c['parameters']['roe_min']==13 and c['baseline_version']==f['id']
    assert new['candidates'][1]['id']==t['id'] and len(new['candidates'])==2
    s=snapshot();s['observations'][0]['financials'][0]['roe']=12.5
    out=fundamental_engines({'snapshot':s,'scope_codes':['SYNTH']},[c],new['active_baselines']['fundamental'])
    assert out['selected_counts'][f['id']]==1 and out['selected_counts'][c['id']]==0
