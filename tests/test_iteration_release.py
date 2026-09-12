import json
import pytest
from mt1.iteration_loop import init, child
from mt1.iteration_release import publish, recover
from test_iteration_validate import frames


def test_actual_switch_rollback_and_recovery(tmp_path):
    r=init(tmp_path/'synthetic','SYNTHETIC_ONLY'); p=r/'evidence.json';p.write_text(json.dumps(frames()))
    args=(r,p,'fundamental','old','new','2026-09-13T00:00:00+00:00')
    with pytest.raises(RuntimeError):publish(*args,fail_at='after_prepare')
    assert not (r/'releases/fundamental/active.json').exists()
    assert recover(r)[0]['action']=='resume_prepared'
    assert publish(*args)['idempotent']
    p.write_text('[]')
    assert recover(r)[0]['action']=='automatic_rollback'
    assert json.loads((r/'releases/fundamental/active.json').read_text())['version']=='old'

def test_reject_forged_caller_pass(tmp_path):
    r=init(tmp_path/'synthetic','SYNTHETIC_ONLY');p=r/'evidence.json';p.write_text('[]')
    assert publish(r,p,'fundamental','old','new','2026-09-13T00:00:00+00:00')['decision']=='continue_shadow'
    assert not (r/'releases/fundamental/active.json').exists()
