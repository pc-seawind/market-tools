"""Full common run state machine with SYNTHETIC_ONLY collection, no gate mocks."""
import pytest
from mt1 import iteration_loop as L
from mt1.iteration_evidence import fundamental,technical
from mt1.action_demo import fixture,append,bundle,make_scope
from mt1.timing_cli import file_hash
from mt1.store import digest as source_digest

@pytest.mark.parametrize('point',['after_prepare','after_switch'])
def test_full_mature_run_pointer_crash_same_command(tmp_path,monkeypatch,point):
    root=tmp_path/'SYNTHETIC_ONLY';scope=tmp_path/'scope.json';make_scope(scope)
    panel=fixture();index=0
    def collect(root,d,*args):
        day=panel['expected_date'];at=panel['fetched_at'];sweep=d/'test-sweep'
        stocks=[{'ts_code':'SYNTH-'+str(i),'name':'SYNTHETIC_ONLY'} for i in range(20)]
        daily=[{'ts_code':s['ts_code'],'trade_date':day.replace('-',''),'pe_ttm':15,'pb':2,'turnover_rate':1} for s in stocks]
        for name,rows in [('stock_basic',stocks),('daily_basic',daily)]:
            L.atomic_json(sweep/(name+'.json'),rows)
            L.atomic_json(sweep/(name+'-source.json'),{'fetched_at':at,'hash':source_digest(rows),'fresh_api':True,'SYNTHETIC_ONLY':True})
        for stock in stocks:
            code=stock['ts_code'];financial=[{'ts_code':code,'ann_date':'20260801','end_date':'20260630','roe':11,'ocfps':1,'eps':1,'debt_to_assets':30}]
            L.atomic_json(sweep/('financial-'+code+'.json'),financial)
            L.atomic_json(sweep/'symbols'/(code+'.json'),{'asof':day,'code':code,'fetched_at':at,'data_hash':source_digest(financial)})
        L.atomic_json(sweep/'summary.json',{'asof':day,'updated_at':at,'classification':'current_snapshot_shadow_not_historical_PIT','fresh_api':True,'universe_hash':source_digest(stocks),'SYNTHETIC_ONLY':True})
        records={}
        for api,field,value in [('daily','close',100-index*.5),('adj_factor','adj_factor',1)]:
            path=d/(api+'.csv');path.write_text('ts_code,trade_date,'+field+'\n'+''.join(s['ts_code']+','+day.replace('-','')+','+str(value)+'\n' for s in stocks))
            records[api]={'path':str(path),'sha256':file_hash(path),'fetched_at':at,'returncode':0}
        return {'fundamental':fundamental(sweep,d/'fundamental',at),
                'technical':technical(bundle(panel,d,index),d/'technical',at),
                'marks':{'records':records,'date':day,'not_PIT_or_execution':True},
                'scope_path':str(scope),'source_scope_hash':file_hash(scope),'asof':at.replace('18:00:00','18:00:00.000001'),'gaps':[]}
    monkeypatch.setattr(L,'collect_inputs',collect)
    for index in range(62):
        kw=dict(sweep=None,scope=scope,request_id='synthetic-'+str(index),_kind='SYNTHETIC_ONLY')
        if index==61:
            with pytest.raises(RuntimeError,match='injected_'+point):L.run(root,fail_at=point,**kw)
            with pytest.raises(ValueError,match='uncommitted'):L.verify(root)
            result=L.run(root,**kw)
        else:result=L.run(root,**kw)
        if index<61:panel=append(panel,100,100)
    assert result['strategy_decisions'][0]['decision']=='experimental_activate'
    assert result['strategy_decisions'][0]['independent_events']==20
    assert not L.verify(root)['incomplete']
    before={str(p):file_hash(p) for p in root.rglob('*') if p.is_file()}
    assert L.run(root,**kw)['idempotent']
    assert before=={str(p):file_hash(p) for p in root.rglob('*') if p.is_file()}
    assert len(list((root/'releases/fundamental/intents').glob('*.committed')))==1
    assert L.read(root/'.mt14.json')['kind']=='SYNTHETIC_ONLY'
