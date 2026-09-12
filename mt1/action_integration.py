"""Fail-isolated report consumer. No scheduling, publishing or quote acquisition."""
from pathlib import Path
from .action_loop import snapshots,read,write,now
from .action_report import compose,publish_weekly
from .timing_cli import file_hash


def cycle(phase,root,out,first=None,second=None,company=None,asof=None):
    if phase not in ('morning','evening','weekly'):raise ValueError('unsupported_report_phase')
    out=Path(out);out.mkdir(parents=True,exist_ok=False)
    result={'phase':phase,'not_published':True,'collection_role':'separate run/execution-worker',
            'production_schedule_unchanged':True,'started_at':now()}
    try:
        if phase=='weekly':
            result['report']=publish_weekly(root,asof or now(),out/'weekly.json')
        else:
            manifest,s=snapshots(root)[-1]
            result['report']=compose(first,second,company,manifest,out/'daily.md')
            result['technical_asof']=s['asof'];result['quote_dates']=sorted({c['quote_date'] for c in s['cards']})
            result['execution_model']=s.get('execution_model','strict-open-v1')
        result['status']='technical_report_ready'
    except Exception as e:
        result.update(status='technical_failed_base_report_unblocked',error_type=type(e).__name__)
        if phase!='weekly':
            # A technical failure never suppresses the caller's already built
            # three sections. Do not fabricate missing market/research sections.
            sections=[Path(p).read_text() for p in (first,second,company)]
            text='\n\n'.join(sections)+'\n\n**技术模块本轮未完成，原行情与研究照常保留；不是无风险或无信号。**\n'
            target=out/'base-report-with-warning.md';write(target,text)
            result['fallback']={'path':str(target),'sha256':file_hash(target)}
        else:
            target=out/'weekly-status.md';write(target,'**技术周报未完成，原周报其他部分不受影响。**\n')
            result['fallback']={'path':str(target),'sha256':file_hash(target)}
    result['finished_at']=now();write(out/'consumer-receipt.json',result)
    return result
