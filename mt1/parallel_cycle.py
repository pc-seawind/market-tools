"""Deterministic shadow production sidecar; no trading/ledger writes."""
import fcntl
import json
from datetime import datetime,timezone
from pathlib import Path
from .data import HERE
from .parallel_collect import collect
from .parallel import run_parallel
from .parallel_bridge import close_review


def cycle(state_dir,phase,panel=None,reviews=None):
    from zoneinfo import ZoneInfo
    state=Path(state_dir);now=datetime.now(timezone.utc)
    today=now.astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat()
    panel=Path(panel) if panel else state/'parallel-panels'/today
    state.mkdir(parents=True,exist_ok=True)
    with (state/'parallel-cycle.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        collect(panel)
        asof=json.loads((panel/'sessions.json').read_text())[-1]
        day=f'{asof[:4]}-{asof[4:6]}-{asof[6:]}'
        if not (state/'sweeps'/day/'daily_basic.json').exists():
            raise ValueError('same-session financial sweep missing; no stale substitution')
        stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        out=state/'parallel-runs'/f'{stamp}-{phase}'
        inbox=Path(reviews) if reviews else state/'parallel-review-inbox.json'
        result=run_parallel(state,panel,out,inbox if inbox.exists() else None)
        result['binding']=close_review(out,state/'plans.db')
        from .forward import run as forward_run
        result['forward']=forward_run(state/'forward',out,panel)
        (out/'forward-harvest.json').write_text(json.dumps(result['forward'],ensure_ascii=False,indent=2))
        result['phase']=phase
        result['execution']={'mode':'live_current_shadow','ledger_write':False,'trade':False,
                             'source':'parallel-cycle CLI','report':str(out/'report.md')}
        (out/'cycle.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
        print('RESULT_JSON='+str(out/'cycle.json'),flush=True)
        return result
