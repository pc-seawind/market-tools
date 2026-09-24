import csv
from datetime import date
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import pytest
import recap_daily_basic as b
import recap_rpc as rpc
import recap_observability as obs
import tushare


def row(code='A',day='20260924'):
    return dict(ts_code=code,trade_date=day,pe_ttm='20',pb='2',total_mv='10000',turnover_rate='1.1')


def setup(tmp_path,monkeypatch,items=None):
    monkeypatch.setenv('RECAP_INPUT_HASH','test')
    monkeypatch.setenv('RECAP_DAILY_BASIC',str(tmp_path/'panel.json'))
    monkeypatch.setattr(tushare,'CACHE_DIR',str(tmp_path/'cache'))
    monkeypatch.setattr(tushare,'_CACHE_DISABLED',False)
    monkeypatch.delenv('RECAP_RPC_CACHE',raising=False)
    b.prepare(tmp_path/'panel.json','20260924','test',lambda *a:items or [row()])
    return dict(ts_code='A',trade_date='20260924',fields=b.FIELDS)


def test_projection_equivalent_nulls_missing_fallback_history_untouched(tmp_path,monkeypatch):
    params=setup(tmp_path,monkeypatch,[{**row(), 'pb':''}])
    assert b.project('daily_basic',params)==[dict(pe_ttm='20',pb='',total_mv='10000',turnover_rate='1.1')]
    assert b.project('daily_basic',{**params,'ts_code':'absent'}) is None
    assert b.project('daily_basic',{'ts_code':'A','fields':'trade_date,pe_ttm'}) is None
    assert b.project('daily',params) is None
    monkeypatch.setenv('RECAP_INPUT_HASH','other');assert b.project('daily_basic',params) is None


@pytest.mark.parametrize('items', [[],[row(),row()],[row(day='20990101')],[{'ts_code':'A'}]])
def test_invalid_batch_is_not_published(tmp_path,items):
    with pytest.raises(ValueError):b.prepare(tmp_path/'panel','20260924','test',lambda *a:items)
    assert not (tmp_path/'panel').exists()


def test_pagination_and_failed_terminal_no_false_complete(tmp_path):
    calls=[]
    def fetch(args,api,params):
        calls.append(params)
        return [row(str(i)) for i in range(6000)] if len(calls)==1 else [row('last')]
    v=b.prepare(tmp_path/'good','20260924','test',fetch)
    assert len(v['rows'])==6001 and calls[1]['offset']=='6000'
    calls.clear()
    def failed(args,api,params):
        calls.append(params);return [row(str(i)) for i in range(6000)] if len(calls)==1 else []
    with pytest.raises(ValueError,match='unproven_terminal'):b.prepare(tmp_path/'bad','20260924','test',failed)
    assert not (tmp_path/'bad').exists()


def test_exact_cache_revision_wins_over_new_batch(tmp_path,monkeypatch):
    params=setup(tmp_path,monkeypatch)
    body={'code':0,'data':{'fields':b.FIELDS.split(','),'items':[[20,2,10000,0.9]]}}
    tushare._cache_write('daily_basic',{'ts_code':'A','trade_date':'20260924'},b.FIELDS,body)
    assert b.project('daily_basic',params) is None
    # Even a previous-day permanent snapshot must retain original vintage.
    for p in (tmp_path/'cache').rglob('*.json'):os.utime(p,(time.time()-86400,)*2)
    assert b.project('daily_basic',params) is None


def test_run_cache_wins_and_batch_missing_uses_original_failure(tmp_path,monkeypatch):
    params=setup(tmp_path,monkeypatch);monkeypatch.setenv('RECAP_RPC_CACHE',str(tmp_path/'run'))
    monkeypatch.setattr(rpc,'provider_cache_csv',lambda *a:None)
    monkeypatch.setattr(rpc,'run',lambda *a,**k:pytest.fail('should not fetch batch-covered name'))
    result=rpc.csv_rpc(['A'],'daily_basic',params)
    assert result[0]['turnover_rate']=='1.1'
    # Mutating panel must not override an existing same-run exact result.
    p=tmp_path/'panel.json';value=json.loads(p.read_text());value['rows']['A']['turnover_rate']='2';p.write_text(json.dumps(value))
    assert rpc.csv_rpc(['A'],'daily_basic',params)==result
    monkeypatch.setattr(rpc,'run',lambda args,**kw:subprocess.CompletedProcess(args,1,'','unavailable'))
    assert rpc.csv_rpc(['B'],'daily_basic',{**params,'ts_code':'B'})==[]
    assert rpc.context.failed


def test_stage_wall_and_real_tushare_counter_path(tmp_path,monkeypatch,capsys):
    trace=tmp_path/'trace.jsonl';monkeypatch.setenv('RECAP_OBSERVABILITY',str(trace))
    monkeypatch.setenv('RECAP_STAGE','score');monkeypatch.setenv('RECAP_STAGE_ID','synthetic')
    monkeypatch.setenv('TUSHARE_TOKEN','not-a-real-token');monkeypatch.setenv('TUSHARE_NO_PARQUET','1')
    monkeypatch.setattr(tushare,'CACHE_DIR',str(tmp_path/'cache'));monkeypatch.setattr(tushare,'_CACHE_DISABLED',False)
    monkeypatch.setattr(tushare,'_parquet',None)  # no persistent production side effects
    body={'code':0,'data':{'fields':['trade_date','close'],'items':[['20260924',1]]}}
    monkeypatch.setattr(tushare.urllib.request,'urlopen',lambda *a,**k:io.BytesIO(json.dumps(body).encode()))
    obs.emit('stage_start');start=time.monotonic()
    args=['daily','ts_code=FAKE','trade_date=20260924','--fields=trade_date,close','--csv']
    assert tushare.main(args)==0;assert tushare.main(args)==0
    obs.emit('stage_finish',wall_seconds=time.monotonic()-start,status='ok',returncode=0)
    record=obs.summary(trace)['stages'][0]
    assert record['counts']['tushare_http_start']==1
    assert record['counts']['tushare_cache:miss']==1 and record['counts']['tushare_cache:hit']==1
    assert record['wall_seconds']>=0 and record['started_at']<=record['finished_at']


def test_unfinished_is_unknown_not_summed_rpc(tmp_path,monkeypatch):
    path=tmp_path/'trace';monkeypatch.setenv('RECAP_OBSERVABILITY',str(path));monkeypatch.setenv('RECAP_STAGE_ID','k')
    obs.emit('stage_start');obs.emit('rpc',event='finish',elapsed_seconds=200)
    s=obs.summary(path)['stages'][0];assert s['status']=='unfinished' and 'wall_seconds' not in s


def test_default_shell_batch_stage_route(tmp_path):
    """Actual shell/stage runner/RPC/CLI, synthetic transport only, zero network."""
    import shutil
    from concepts_data import CONCEPTS
    root=Path(__file__).resolve().parents[1]
    provider=tmp_path/'provider';provider.mkdir()
    for name in ['evening_recap_data.sh','recap_observability.py','recap_daily_basic.py']:
        shutil.copyfile(root/name,provider/name)
    concepts=['存储芯片 (HBM/DDR/NAND)','有色金属']
    codes=sorted({c for concept in concepts for c,_ in CONCEPTS[concept]})
    assert len(codes)>=12
    day=date.today().strftime('%Y%m%d')
    (provider/'sector_score.py').write_text('import json;print(json.dumps('+repr([
        dict(concept=c,tier1_pass=True,total_score=60) for c in concepts])+'))')
    htsc=provider/'htsc_sector_flow.py';htsc.write_text("from recap_observability import emit;emit('htsc_cache',outcome='hit');print('{}')");htsc.chmod(0o755)
    transport='''import io,json,runpy,sys,urllib.request

def fake(req,**kw):
 p=json.loads(req.data);api=p['api_name']
 if api=='index_daily':f=['trade_date'];rows=[[DAY]]
 elif api=='trade_cal':f=['cal_date','is_open'];rows=[[DAY,1]]
 elif api=='daily_basic':
  f=p['fields'].split(',');rows=[[c,DAY,20,2,10000,1.1] for c in CODES]
 else:raise AssertionError('unexpected provider '+api)
 return io.BytesIO(json.dumps({'code':0,'data':{'fields':f,'items':rows}}).encode())
urllib.request.urlopen=fake
runpy.run_path(TUSHARE,run_name='__main__')
'''.replace('DAY',repr(day)).replace('CODES',repr(codes)).replace('TUSHARE',repr(str(root/'tushare.py')))
    (provider/'tushare.py').write_text(transport)
    (provider/'sector_picks.py').write_text('''import json,sys
from sector_picks import _ts
from recap_daily_basic import FIELDS
rows=_ts('daily_basic',ts_code=CODE,trade_date=DAY,fields=FIELDS)
assert rows and rows[0]['turnover_rate']=='1.1'
print(json.dumps({'evaluations':[{'stock':{'code':CODE,'trade_date':DAY}}]}))
'''.replace('from sector_picks import _ts',
            "import importlib.util\nsys.path.insert(0, "+repr(str(root))+")\nspec=importlib.util.spec_from_file_location('real_picks', "+repr(str(root/'sector_picks.py'))+");m=importlib.util.module_from_spec(spec);sys.modules['real_picks']=m;spec.loader.exec_module(m);_ts=m._ts")
       .replace('CODE',repr(codes[0])).replace('DAY',repr(day)))
    output=tmp_path/'out.json'
    env={**os.environ,'PYTHONPATH':str(root),'TUSHARE_TOKEN':'synthetic-not-valid',
         'TUSHARE_CACHE_DIR':str(tmp_path/'cache'),'TUSHARE_NO_PARQUET':'1',
         'EVENING_RECAP_BUDGET_SECONDS':'30','EVENING_RECAP_REVERSAL_QUOTA':'0'}
    cp=subprocess.run(['bash',str(provider/'evening_recap_data.sh'),'--out',str(output)],
                      env=env,capture_output=True,text=True,timeout=40)
    assert cp.returncode==0,cp.stderr
    value=json.loads(output.read_text())
    assert value['meta']['daily_basic_batch']['returncode']==0
    assert value['meta']['status']=='complete'
    performance=obs.summary(str(output)+'.performance.jsonl')
    stages={r['stage']:r for r in performance['stages']}
    assert all(r['status']=='ok' for r in stages.values())
    assert stages['htsc_refresh']['counts']['htsc_cache:hit']==1
    assert stages['picks']['counts']['tushare_http_start']==3  # freshness + calendar + one panel
    assert sum(r['counts'].get('logical_cache_hit:run_daily_basic',0) for r in stages.values())>=1


@pytest.mark.parametrize('change', ['expired','future_clock','previous_day','wrong_date','no_cache'])
def test_stale_panel_never_served(tmp_path,monkeypatch,change):
    params=setup(tmp_path,monkeypatch);p=tmp_path/'panel.json';v=json.loads(p.read_text())
    if change=='expired':v['fetched_at']=time.time()-1801
    elif change=='future_clock':v['fetched_at']=time.time()+60
    elif change=='previous_day':v['created_day']='2000-01-01'
    elif change=='wrong_date':params['trade_date']='20260923'
    else:monkeypatch.setenv('TUSHARE_NO_CACHE','1')
    p.write_text(json.dumps(v));assert b.project('daily_basic',params) is None


def test_stock_detail_cannot_override_phase_or_break_telemetry(tmp_path,monkeypatch):
    p=tmp_path/'trace';monkeypatch.setenv('RECAP_OBSERVABILITY',str(p))
    monkeypatch.setenv('RECAP_STAGE','picks');monkeypatch.setenv('RECAP_STAGE_ID','pick-phase')
    rpc.trace({'stage':'stock','stock':'FAKE','elapsed_seconds':1,'status':'ok'})
    row=json.loads(p.read_text());assert row['stage']=='picks' and row['detail_stage']=='stock'


def test_score_memory_hit_is_separate_from_http(tmp_path,monkeypatch):
    import sector_score
    p=tmp_path/'trace';monkeypatch.setenv('RECAP_OBSERVABILITY',str(p))
    monkeypatch.setenv('RECAP_STAGE','score');monkeypatch.setenv('RECAP_STAGE_ID','score-memo')
    monkeypatch.setattr(sector_score,'_fina_cache',{'FAKE':{'roe':20},'missing':{}})
    assert sector_score._fetch_fina('FAKE')=={'roe':20}
    assert sector_score._fetch_fina('missing')=={}
    rows=[json.loads(x) for x in p.read_text().splitlines()]
    assert [r['usable'] for r in rows]==[True,False]
    assert obs.summary(p)['stages'][0]['counts']=={'score_fina_memory_hit':2}
