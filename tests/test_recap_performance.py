import csv
import json
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import recap_rpc as rpc
import tushare


def cached(tmp_path, monkeypatch, body=None):
    monkeypatch.setattr(tushare, 'CACHE_DIR', str(tmp_path))
    monkeypatch.setattr(tushare, '_CACHE_DISABLED', False)
    monkeypatch.setenv('TUSHARE_TOKEN', 'test-only')
    monkeypatch.delenv('TUSHARE_NO_CACHE', raising=False)
    params = {'ts_code': '000001.SZ', 'fields': 'trade_date,close'}
    args = ['python3', str(Path(tushare.__file__)), 'daily', 'ts_code=000001.SZ', '--fields=trade_date,close', '--csv']
    body = body or {'code': 0, 'data': {'fields': ['trade_date', 'close'], 'items': [['20260924', 1.25], ['20260923', None]]}}
    tushare._cache_write('daily', {'ts_code': '000001.SZ'}, params['fields'], body)
    path = tmp_path/'daily'/(tushare._cache_key('daily', {'ts_code':'000001.SZ'}, params['fields'])+'.json')
    return args, params, path, body


def test_exact_cache_matches_cli_wire_and_skips_process(tmp_path, monkeypatch, capsys):
    args, params, path, body = cached(tmp_path, monkeypatch)
    monkeypatch.setattr(rpc, 'run', lambda *a, **k: pytest.fail('cache must not start CLI'))
    assert tushare.main(args[2:]) == 0
    expected = list(csv.DictReader(capsys.readouterr().out.splitlines()))
    assert rpc.csv_rpc(args, 'daily', params) == expected
    assert expected == [{'trade_date':'20260924', 'close':'1.25'}, {'trade_date':'20260923', 'close':''}]


@pytest.mark.parametrize('case', ['yesterday', 'future_mtime', 'expired', 'different_fields', 'disabled', 'negative', 'corrupt', 'empty', 'future_vintage'])
def test_no_unsafe_reuse(tmp_path, monkeypatch, case):
    args, params, path, body = cached(tmp_path, monkeypatch)
    if case == 'yesterday': os.utime(path, (time.time()-86400,)*2)
    elif case == 'future_mtime': os.utime(path, (time.time()+10,)*2)
    elif case == 'expired': monkeypatch.setattr(tushare, '_cache_ttl', lambda *a: -1)
    elif case == 'different_fields': params['fields'] += ',vol'
    elif case == 'disabled': monkeypatch.setenv('TUSHARE_NO_CACHE','1')
    elif case == 'negative': path.write_text(json.dumps({'code':40203,'_neg_ttl':600}))
    elif case == 'corrupt': path.write_text('not json')
    elif case == 'empty': path.write_text(json.dumps({'code':0,'data':{'fields':['x'],'items':[]}}))
    elif case == 'future_vintage':
        body['data']['items'][0][0]='20991231';path.write_text(json.dumps(body))
        assert rpc.csv_rpc(args,'daily',params)==[]
        return
    assert rpc.provider_cache_csv(args,'daily',params) is None


def test_singleflight_success_only_and_input_isolation(tmp_path, monkeypatch):
    monkeypatch.setenv('RECAP_RPC_CACHE', str(tmp_path/'run'))
    calls=[]
    def run(args, **kw):
        calls.append(args);time.sleep(.03)
        return subprocess.CompletedProcess(args,0,'trade_date,close\n20260924,1\n','')
    monkeypatch.setattr(rpc,'run',run)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(lambda _:rpc.csv_rpc(['same'],'daily',{}),range(4)))
    assert len(calls)==1 and all(x==results[0] for x in results)
    monkeypatch.setenv('RECAP_INPUT_HASH','different')
    rpc.csv_rpc(['same'],'daily',{})
    assert len(calls)==2
    monkeypatch.setattr(rpc,'run',lambda args,**kw: subprocess.CompletedProcess(args,1,'','failed'))
    assert rpc.csv_rpc(['fail'],'daily',{})==[]
    assert len(list((tmp_path/'run').glob('*.json')))==2


def test_full_history_order_nulls_preserved(tmp_path, monkeypatch):
    body={'code':0,'data':{'fields':['trade_date','close'], 'items':[['20260924',i if i%5 else None] for i in range(3500)]}}
    args,params,_,_=cached(tmp_path,monkeypatch,body)
    rows=rpc.csv_rpc(args,'daily',params)
    assert len(rows)==3500 and rows[-1]['close']=='3499'


@pytest.mark.parametrize('failed_code', [None, '301308.SZ'])
def test_full_sector_frozen_scores_signals_and_partial_coverage(tmp_path, monkeypatch, failed_code):
    """Reference CSV boundary vs direct cache, through actual ranking/evaluate."""
    import copy
    from datetime import date, timedelta
    from types import SimpleNamespace
    import sector_picks as picks
    from sector_score import SectorScore
    root=Path(__file__).resolve().parents[1]
    archived=json.loads((root/'reports/recap-reliability-20260925/fixed.result.json').read_text())
    concept=archived['concept'];sig=archived['sector_signals']
    monkeypatch.setattr(picks,'score_sector',lambda _:SectorScore(**archived['sector_score']))
    monkeypatch.setattr(picks,'sector_signals',lambda _:SimpleNamespace(
        nav_pct_5d=sig['nav_5d'],nav_pct_1m=sig['nav_1m'],flow_5d_cny=sig['flow_5d_cny'],
        flow_20d_cny=sig['flow_20d_cny'],pct_rank_60d=sig['pct_rank_60d'],pct_rank_250d=sig['pct_rank_250d']))
    monkeypatch.setattr(picks,'_SPBT_AVAILABLE',False)
    monkeypatch.setenv('RECAP_NO_HISTORY','1');monkeypatch.delenv('RECAP_SCORE_FILE',raising=False)
    monkeypatch.delenv('RECAP_RPC_CACHE',raising=False)
    monkeypatch.setattr(tushare,'CACHE_DIR',str(tmp_path));monkeypatch.setattr(tushare,'_CACHE_DISABLED',False)
    monkeypatch.setenv('TUSHARE_TOKEN','fixture');monkeypatch.delenv('TUSHARE_NO_CACHE',raising=False)
    frozen={}
    days=[(date(2026,9,24)-timedelta(days=i)).strftime('%Y%m%d') for i in range(360)]
    for code,_ in picks.get_sector_stocks(concept):
        specs=[('daily','trade_date,close,vol,amount',{},[[d,100+i*.1,1000+i,100000] for i,d in enumerate(days)]),
          ('adj_factor','trade_date,adj_factor',{},[[d,2 if i<30 else 1] for i,d in enumerate(days)]),
          ('daily_basic','pe_ttm,pb,total_mv,turnover_rate',{'trade_date':'20260924'},[[20,2,1000000,1.3]]),
          ('daily_basic','trade_date,pe_ttm',{},[[d,20+i*.01] for i,d in enumerate(days)]),
          ('fina_indicator','ann_date,end_date,roe,grossprofit_margin,netprofit_yoy,or_yoy',{},
           [['20260830','20260630',20,30,45,40],['20250430','20250331',18,28,40,35]])]
        for api,fields,extra,items in specs:
            params={'ts_code':code,**extra}
            body={'code':0,'data':{'fields':fields.split(','),'items':items}}
            tushare._cache_write(api,params,fields,body)
            frozen[(api,code,fields)]=list(csv.DictReader(tushare.csv_text(body['data']).splitlines()))
    original_ts=picks._ts
    def reference(api,**params):
        if params.get('ts_code')==failed_code and api=='daily':
            rpc.context.failed=True
            return []
        return copy.deepcopy(frozen[(api,params['ts_code'],params['fields'])])
    monkeypatch.setattr(picks,'_ts',reference)
    expected=picks.sector_picks(concept)
    monkeypatch.setattr(picks,'_ts',original_ts)
    if failed_code:
        fields='trade_date,close,vol,amount'
        (tmp_path/'daily'/(tushare._cache_key('daily',{'ts_code':failed_code},fields)+'.json')).write_text('{}')
    monkeypatch.setattr(rpc,'run',lambda args,**kw:subprocess.CompletedProcess(args,1,'','simulated failure'))
    actual=picks.sector_picks(concept)
    assert actual==expected
    assert actual['coverage']['successful']==(6 if failed_code else 7)
    assert actual['coverage']['missing']==([failed_code] if failed_code else [])


def test_singleflight_across_processes(tmp_path):
    import sys
    script = '''
import os,sys,time,subprocess
import recap_rpc as rpc
os.environ['RECAP_RPC_CACHE']=sys.argv[1]
def provider(args,**kw):
    with open(sys.argv[2],'a') as f: f.write('provider\\n')
    time.sleep(.1)
    return subprocess.CompletedProcess(args,0,'trade_date,close\\n20260924,1\\n','')
rpc.run=provider
assert rpc.csv_rpc(['identical'],'daily',{})==[{'trade_date':'20260924','close':'1'}]
'''
    args=[sys.executable,'-c',script,str(tmp_path/'cache'),str(tmp_path/'calls')]
    children=[subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE) for _ in range(2)]
    for child in children:
        out,err=child.communicate(timeout=10)
        assert child.returncode==0,err
    assert (tmp_path/'calls').read_text().splitlines()==['provider']
