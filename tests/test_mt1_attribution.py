import hashlib
import pytest
from mt1.conflict_attribution import classify_values,ratio_diagnostic
from mt1.suspension_evidence import validate_interval,explain


def test_rounding_compatibility_is_not_admission():
    r=classify_values(['113.9362','113.936']);assert r['kind']=='compatible_with_3dp_rounding' and not r['admissible']

def test_real_change_not_called_rounding():
    assert classify_values([10,11])['kind']=='substantive_difference'

def test_rounding_half_up():
    assert classify_values(['27.6285','27.629'])['kind']=='compatible_with_3dp_rounding'

def test_scale_not_authorized():
    assert not ratio_diagnostic([(1,2),(2,4)])['normalization_authorized']
    assert not ratio_diagnostic([(1,2),(2,5)])['exact_constant_scale']

def fixture(tmp_path,resume='2023年3月13日起复牌'):
    def ref(name,text):
        p=tmp_path/name;p.write_text(text);return {'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
    return {'code':'920039.BJ','old_code':'831039','start':'2023-03-01','resume':'2023-03-13',
        'text':ref('t','831039 2023年3月1日起停牌 '+resume),'pdf':ref('p','%PDF test fixture'),
        'mapping_text':ref('m','国义招标 831039 920039')}

def test_interval_does_not_fill_rows_or_authorize_trade(tmp_path):
    e=fixture(tmp_path);r=explain([{'code':'920039.BJ','day':'2023-03-10'}],[e])[0]
    assert r['explanation']=='issuer_confirmed_suspension' and not r['daily_row_filled'] and not r['PIT_permission']

def test_expected_resume_not_actual(tmp_path):
    with pytest.raises(ValueError,match='actual resumption'):validate_interval(fixture(tmp_path,'预计将于2023年3月13日前复牌'))

def test_resume_day_excluded(tmp_path):
    assert explain([{'code':'920039.BJ','day':'2023-03-13'}],[fixture(tmp_path)])[0]['explanation']!='issuer_confirmed_suspension'

def test_wrong_alias_rejected(tmp_path):
    e=fixture(tmp_path);e['code']='920090.BJ'
    with pytest.raises(ValueError,match='alias'):validate_interval(e)

def test_mutated_source_rejected(tmp_path):
    e=fixture(tmp_path);e['text']['sha256']='bad'
    with pytest.raises(ValueError,match='hash'):validate_interval(e)


def test_constant_level_cannot_identify_normalization():
    assert not ratio_diagnostic([(10,20),(10,20)])['scale_identifiable_from_variation']

def test_expected_on_resume_rejected(tmp_path):
    with pytest.raises(ValueError,match='actual resumption'):validate_interval(fixture(tmp_path,'预计于2023年3月13日起复牌'))
