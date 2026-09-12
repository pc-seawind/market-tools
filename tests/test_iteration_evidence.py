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
