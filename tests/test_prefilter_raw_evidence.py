import io
import sys
from pathlib import Path
from unittest.mock import patch
import pytest
import cls_telegraph_filter as target


def test_response_saved_before_parsing(tmp_path):
    output = tmp_path / 'response.txt'
    raw = '{"original":"未过滤响应"}'
    with patch.object(sys, 'argv', ['filter', '--source', 'sina', '--raw-output', str(output)]), patch.object(sys, 'stdin', io.StringIO(raw)), patch.object(target, 'load_universe_index', side_effect=RuntimeError('stop before parse')):
        with pytest.raises(RuntimeError):
            target.main()
    assert output.read_text() == raw


def test_evidence_never_overwritten(tmp_path):
    output = tmp_path / 'response.txt'
    output.write_text('original')
    with patch.object(sys, 'argv', ['filter', '--raw-output', str(output)]), patch.object(sys, 'stdin', io.StringIO('new response')):
        with pytest.raises(FileExistsError):
            target.main()
    assert output.read_text() == 'original'


def test_shell_persists_outside_temporary_directory():
    script = (Path(target.__file__).parent / 'narrative_prefilter.sh').read_text()
    assert '--raw-output "$RAW_DIR/$src.response.txt"' in script
    assert 'mktemp -d "$RAW_ROOT/$TODAY/run-XXXXXXXX"' in script
