from datetime import datetime, timezone
import pytest
from mt1.iteration_evidence import retain, verify_sources, check_time

def test_future_expired_and_hash(tmp_path):
    asof='2026-09-13T00:00:00+00:00'
    for t in ['2026-09-14T00:00:00+00:00','2026-08-01T00:00:00+00:00']:
        with pytest.raises(ValueError):check_time(t,asof)
    p=tmp_path/'source';p.write_text('original')
    m=retain(p,tmp_path/'archive',asof,asof)
    verify_sources([m],asof)
    m['sha256']='f'*64
    with pytest.raises(ValueError):verify_sources([m],asof)


def test_sweep_material_reconstruction_and_forged_snapshot(tmp_path):
    import json,copy
    from mt1.store import digest as sd
    from mt1.timing import digest
    from mt1.iteration_evidence import fundamental,verify_fundamental
    r=tmp_path/'sweep';r.mkdir();(r/'symbols').mkdir();at='2026-09-11T18:00:00+00:00'
    universe=[{'ts_code':'SYNTH','name':'fixture'}]
    daily=[{'ts_code':'SYNTH','trade_date':'20260911','pe_ttm':15,'pb':2,'turnover_rate':1}]
    financial=[{'ts_code':'SYNTH','ann_date':'20260801','end_date':'20260630','roe':11,'ocfps':1,'eps':1,'debt_to_assets':30}]
    def write(name,v):(r/name).write_text(json.dumps(v))
    for name,rows in [('stock_basic',universe),('daily_basic',daily)]:
        write(name+'.json',rows);write(name+'-source.json',{'fetched_at':at,'hash':sd(rows),'fresh_api':True})
    write('financial-SYNTH.json',financial)
    write('symbols/SYNTH.json',{'asof':'2026-09-11','code':'SYNTH','fetched_at':at,'data_hash':sd(financial)})
    write('summary.json',{'asof':'2026-09-11','updated_at':at,'classification':'current_snapshot_shadow_not_historical_PIT','fresh_api':True,'universe_hash':sd(universe)})
    e=fundamental(r,tmp_path/'out','2026-09-13T00:00:00+00:00');assert verify_fundamental(e)
    forged=copy.deepcopy(e);forged['snapshot']['observations'][0]['financials'][0]['roe']=100
    forged['snapshot_hash']=digest(forged['snapshot'])
    with pytest.raises(ValueError,match='not_derived'):verify_fundamental(forged)
