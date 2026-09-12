import pytest
from mt1.iteration_policy import parameters, BASE, candidate, action_policy
from mt1.action_loop import read, POLICY

@pytest.mark.parametrize('bad', [{'oos_pass': True}, {'min_independent_events': 1}, {'roe_min': 0}, {'roe_min': float('nan')}, {'roe_min': True}])
def test_whitelist(bad):
    with pytest.raises(ValueError): parameters('fundamental', bad)

def test_default_and_frozen_risk():
    assert parameters('fundamental') == BASE['fundamental']
    c=candidate('technical', {'breakout_volume_min':1.4}, 'volume selectivity', 'abc', '2026-09-14T00:00:00+00:00')
    baseline=read(POLICY); new=action_policy(c)
    assert new['timing']['atr_multiple'] == baseline['timing']['atr_multiple']
    assert new['timing']['execution'] == baseline['timing']['execution']
    assert new['timing']['breakout_volume_min'] == 1.4
