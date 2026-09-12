import pytest
from mt1.longitudinal import aware_time,expected_coverage

@pytest.mark.parametrize('value',[None,123,{},[],True])
def test_invalid_timestamp_type_is_data_gap(value):
    with pytest.raises(ValueError):aware_time(value)

@pytest.mark.parametrize('field',['started_at','completed_at'])
def test_null_execution_time_keeps_archive_unverified(field):
    item=dict(execution_id='TEST',job='test',run_id='run',scope_epoch='epoch',market=None,trade_date='2026-09-12',scheduled_at='2026-09-12T07:15:00+08:00',deadline_at='2026-09-12T09:15:00+08:00',calendar=dict(mode='always',source='SYNTHETIC test',verified_at='2026-09-11T20:00:00+08:00'))
    execution=dict(execution_id='TEST',started_at='2026-09-12T07:16:00+08:00',completed_at='2026-09-12T07:25:00+08:00',source='SYNTHETIC test',status='ok');execution[field]=None
    manifest={**{k:item[k] for k in ['job','run_id','scope_epoch','trade_date']},'execution':execution,'recorded_at':'2026-09-12T07:30:00+08:00','status':'partial'}
    result=expected_coverage({'expected':[item]},[('unused','test-manifest',manifest)],aware_time('2026-09-12T22:00:00+08:00'),False)
    assert result['status']=='partial'
    assert result['rows'][0]['status']=='execution_unverified'
    assert result['rows'][0]['manifests']==['test-manifest']
    assert result['rows'][0]['evidence_errors']
    assert manifest['execution'][field] is None
