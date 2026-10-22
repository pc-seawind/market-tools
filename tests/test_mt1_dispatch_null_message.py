"""Exercise the actual remote collector with journal MESSAGE=null."""
import json
import os
import shlex
import subprocess
from pathlib import Path

from mt1.evidence import collect_dispatch


def test_collect_dispatch_skips_null_journal_message(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    cron = home / '.homespace' / 'cron'
    cron.mkdir(parents=True)
    (cron.parent / 'cron-managed-threads.json').write_text('{}')
    (cron / 'evening-market-recap.json').write_text('{}')
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    journal = bin_dir / 'journalctl'
    journal.write_text('#!/bin/sh\nprintf \'%s\\n\' \'{"MESSAGE":null}\' \'{"MESSAGE":"cron: firing evening-market-recap rest", "__REALTIME_TIMESTAMP":"1"}\'\n')
    journal.chmod(0o755)
    run = tmp_path / 'state' / 'runs' / '2026-10-22-evening'
    run.mkdir(parents=True)
    (run / 'report.json').write_text(json.dumps({'asof':'2026-10-22', 'phase':'evening'}))
    real_run = subprocess.run

    def remote_run(argv, **kwargs):
        assert argv[:2] == ['ssh', '-o']
        return real_run(shlex.split(argv[-1]), env={**os.environ, 'HOME':str(home),
                        'PATH':str(bin_dir)+os.pathsep+os.environ['PATH']}, **kwargs)

    monkeypatch.setattr(subprocess, 'run', remote_run)
    result = collect_dispatch(tmp_path / 'state', '2026-10-22-evening')
    assert result['status'] == 'awaiting_unambiguous_dispatch'
    assert len(result['evidence']['logs']) == 1
    assert result['evidence']['logs'][0]['timestamp'] == '1'
