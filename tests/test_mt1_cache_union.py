import json
import pytest
from mt1.cache_union import Union, canonical, merge, discover


def row(**kw):return {'ts_code':'000001.SZ','trade_date':'20240102','open':1,'high':2,'low':1,'close':2,'vol':10,'amount':20,**kw}

def test_canonical():
    assert canonical(1)==canonical('1.000')
    assert canonical(None) is None
    with pytest.raises(ValueError):canonical(float('inf'))

def test_merge_complement():
    assert merge({'close':'2'},{'open':'1','close':'2'})==({'open':'1','close':'2'},[])

def test_conflict_never_last_wins(tmp_path):
    u=Union(tmp_path/'u.db');u.ingest('daily',[row(),row(close=3)],1)
    assert u.get('daily','000001.SZ','20240102')[1]=='source_conflict'
    u.ingest('daily',[row()],2)
    assert u.get('daily','000001.SZ','20240102')[1]=='source_conflict'
    assert u.db.execute('select count(*) from provenance').fetchone()[0]==3

def test_missing_and_null_not_zero(tmp_path):
    u=Union(tmp_path/'u.db');u.ingest('daily',[row(open=None)],1)
    assert u.get('daily','000001.SZ','20240102')[1]=='missing_required_fields'
    assert u.get('daily','000002.SZ','20240102')[1]=='missing_row'

def test_missing_code_not_inferred(tmp_path):
    u=Union(tmp_path/'u.db');assert u.ingest('daily',[row(ts_code=None)],1)['rejected']==1

def test_hash_and_frozen_source(tmp_path):
    out=tmp_path/'out';out.mkdir();p=tmp_path/'input.json';p.write_text(json.dumps([row()]))
    u=Union(tmp_path/'u.db')
    with pytest.raises(ValueError,match='hash mismatch'):u.snapshot(('daily',p,'rows','bad'),out)
    r=u.snapshot(('daily',p,'rows',None),out);p.write_text('[]')
    from pathlib import Path
    assert len(json.loads(Path(r['snapshot']).read_text()))==1

def test_coverage_conflicts_fields_and_absence(tmp_path):
    u=Union(tmp_path/'u.db');u.ingest('daily',[row()],1)
    h=u.coverage({'2024-01-02':['000001.SZ','000002.SZ']},tmp_path/'holes.gz')
    assert h['remaining_holes']==2
    assert h['by_api_reason']['daily:missing_row']==1

def test_no_metadata_conflict(tmp_path):
    u=Union(tmp_path/'u.db');u.ingest('daily',[row(end_date='20240103'),row(end_date='20250103',close='2.000')],1)
    assert u.get('daily','000001.SZ','20240102')[1] is None

def test_order_independent_quarantine(tmp_path):
    for i,rows in enumerate(([row(),row(open=5)],[row(open=5),row()])):
        u=Union(tmp_path/f'{i}.db');u.ingest('daily',rows,1)
        assert u.get('daily','000001.SZ','20240102')[1]=='source_conflict'

def test_error_and_empty_sources_not_silent_success(tmp_path):
    out=tmp_path/'out';out.mkdir();u=Union(tmp_path/'x.db')
    for i,body,status in [(0,{'code':40203},'provider_error'),(1,{'code':0,'data':{'fields':[],'items':[]}},'empty_response')]:
        p=tmp_path/f'{i}.json';p.write_text(json.dumps(body))
        assert u.snapshot(('daily',p,'response',None),out)['status']==status

def test_unrecognized_response_refused(tmp_path):
    out=tmp_path/'out';out.mkdir();p=tmp_path/'r.json';p.write_text('{}')
    with pytest.raises(ValueError,match='unrecognized'):Union(tmp_path/'u.db').snapshot(('daily',p,'response',None),out)

def test_suspension_not_implied_by_missing(tmp_path):
    u=Union(tmp_path/'u.db')
    h=u.coverage({'2024-01-02':['000001.SZ']},tmp_path/'a.gz')
    assert h['suspension_coverage_unknown']==1
    h=u.coverage({'2024-01-02':['000001.SZ']},tmp_path/'b.gz',{'2024-01-02':[]})
    assert h['unexplained_missing_row']==1
    h=u.coverage({'2024-01-02':['000001.SZ']},tmp_path/'c.gz',{'2024-01-02':[{'ts_code':'000001.SZ'}]})
    assert h['suspension_record_needs_review']==1 and h['remaining_holes']==1
