"""Execute the embedded collector with fake providers; never touches live data."""
import json
from pathlib import Path
import subprocess

SCRIPT = Path(__file__).resolve().parents[1] / 'evening_recap_data.sh'

def run_collector(tmp_path, monkeypatch, *, budget='120', fail=False):
    scores = tmp_path / 'scores.json'
    scores.write_text(json.dumps([{'concept': n, 'tier1_pass': True, 'total_score': 60} for n in ['A','B']]))
    out = tmp_path / 'result.json'
    monkeypatch.setenv('EVENING_RECAP_REMAINING_SECONDS', budget)
    monkeypatch.setattr('sys.argv', ['-', str(scores), str(out), '2', '2026-09-22'])
    calls=[]
    def provider(args, **kwargs):
        if 'sector_picks.py' in args:
            calls.append(args)
            # A valid partial checkpoint must exist BEFORE expensive work.
            checkpoint=json.loads(out.read_text())
            assert checkpoint['meta']['status']=='running'
            assert checkpoint['meta']['errors']
            if fail: raise subprocess.TimeoutExpired(args, kwargs['timeout'])
            return subprocess.CompletedProcess(args,0,'{"evaluations":[]}', '')
        body='trade_date\n20260922\n' if 'index_daily' in args else 'cal_date,is_open\n20260922,1\n'
        return subprocess.CompletedProcess(args,0,body,'')
    monkeypatch.setattr(subprocess, 'run', provider)
    monkeypatch.setattr('recap_runtime.run', provider)
    body=SCRIPT.read_text().split("<<'PY'\n",1)[1].split('\nPY\n',1)[0]
    exec(compile(body, str(SCRIPT), 'exec'), {'__name__':'__main__'})
    return json.loads(out.read_text()),calls

def test_checkpoint_then_success(tmp_path, monkeypatch):
    result,calls=run_collector(tmp_path,monkeypatch)
    assert len(calls)==2
    assert result['meta']['status']=='complete'
    assert result['meta']['errors']==[]

def test_budget_exhausted_retains_scores_and_skips_picks(tmp_path, monkeypatch):
    result,calls=run_collector(tmp_path,monkeypatch,budget='0')
    assert not calls
    assert len(result['scores'])==2
    assert result['meta']['status']=='partial'
    assert result['meta']['n_picks_run']==0
    assert result['meta']['errors']
    assert all('budget' in row['error'] for row in result['picks'].values())

def test_provider_timeout_not_success(tmp_path, monkeypatch):
    result,calls=run_collector(tmp_path,monkeypatch,fail=True)
    assert len(calls)==2
    assert result['meta']['status']=='partial'
    assert len(result['meta']['errors'])==2


def test_shell_syntax_and_budget_validation():
    subprocess.run(['bash', '-n', str(SCRIPT)], check=True)
    import os
    cp = subprocess.run(['bash', str(SCRIPT), '--help'], capture_output=True,
                        env={**os.environ, 'EVENING_RECAP_BUDGET_SECONDS': '0'})
    assert cp.returncode == 2
    assert b'positive integer' in cp.stderr


def test_shell_stage_budget_real_timeout(tmp_path):
    import os
    import shutil
    import time
    script = tmp_path / 'evening_recap_data.sh'
    shutil.copyfile(SCRIPT, script)
    shutil.copyfile(SCRIPT.parent/'recap_observability.py', tmp_path/'recap_observability.py')
    (tmp_path / 'sector_score.py').write_text('import time; time.sleep(10)')
    start = time.monotonic()
    cp = subprocess.run(['bash', str(script), '--out', str(tmp_path / 'out.json')],
                        capture_output=True, timeout=8,
                        env={**os.environ, 'EVENING_RECAP_BUDGET_SECONDS': '1'})
    assert cp.returncode == 4
    assert time.monotonic() - start < 6
    assert b'TIMED OUT' in cp.stderr
