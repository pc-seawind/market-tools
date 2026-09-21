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
    # Research refresh is bounded and asynchronous; failure cannot block MT13.
    if phase!='weekly' and company:
        from .ta_pipeline import request_refresh
        result['research_refresh']=request_refresh(Path(company).parent,phase)
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
    try:
        from .ta_workflow import report_inbox
        result['research_workflow']=report_inbox(out,asof or now())
    except Exception as e:
        result['research_workflow']={'status':'inbox_failed','owner':'investment-agent:existing-daily-and-weekend','reason':type(e).__name__,'base_report_unblocked':True}
    if phase != 'weekly':
        publication_prefix_bytes = 0
        import os
        base = os.environ.get('HUATAI_REPORT_BASE')
        if base:
            try:
                from .huatai_daily import render, save as replace_own_output
                text, receipt = render(base)
                target = out / 'daily.md' if (out / 'daily.md').exists() else out / 'base-report-with-warning.md'
                original = target.read_text()
                # Preserve the independent original judgment and every successful API answer.
                replace_own_output(target, '# 本方日报判断与 TA/MT13\n\n' + original + text)
                publication_prefix_bytes = len('# 本方日报判断与 TA/MT13\n\n'.encode('utf-8'))
                receipt['report_sha256'] = file_hash(target)
                result['huatai'] = receipt
                slot = 'report' if 'report' in result else 'fallback'
                result[slot]['sha256'] = file_hash(target)
                if slot == 'report':
                    replace_own_output(Path(str(target) + '.receipt.json'), result['report'])
                write(out / 'huatai-consumer-receipt.json', receipt)
            except Exception as e:
                result['huatai'] = {'status': 'failed', 'reason': type(e).__name__}
                target = out / 'daily.md' if (out / 'daily.md').exists() else out / 'base-report-with-warning.md'
                warning = '\n\n**华泰咨询模块失败：' + type(e).__name__ + '；原日报保留。本轮未取得完整逐股全文，请勿宣称全部咨询成功。**\n'
                try:
                    from .huatai_daily import stocks, read as ht_read, SCOPE
                    warning += '\n'.join('- ' + s['name'] + ' ' + s['code'] + '：本轮咨询不可用，模块错误 ' + type(e).__name__ for s in stocks(ht_read(SCOPE)))
                except Exception:
                    warning += '\n真实scope也无法读取，无法声称逐股覆盖。'
                replace_own_output(target, target.read_text() + warning)
                slot = 'report' if 'report' in result else 'fallback'
                result[slot]['sha256'] = file_hash(target)
                if slot == 'report': replace_own_output(Path(str(target) + '.receipt.json'), result['report'])
        from .huatai_daily import publication_parts
        target = out / 'daily.md' if (out / 'daily.md').exists() else out / 'base-report-with-warning.md'
        spans = [dict(s, start=s['start'] + publication_prefix_bytes,
                      end=s['end'] + publication_prefix_bytes)
                 for s in result.get('report', {}).get('publication_optional_spans', [])]
        result['publication'] = publication_parts(target, out, optional_spans=spans)
    result['finished_at']=now();write(out/'consumer-receipt.json',result)
    return result


def verify_run(root,capture_dir,report_dirs):
    """Read-only natural-run audit; never infer that no signal means failure/success."""
    import hashlib
    from pathlib import Path
    from .action_loop import snapshots,read
    capture=Path(capture_dir);worker=read(capture/'worker-result.json')
    checked=[]
    for meta in sorted((capture/'sources').glob('*.meta.json')):
        m=read(meta);raw=meta.with_name(meta.name.replace('.meta.json','.raw'))
        if hashlib.sha256(raw.read_bytes()).hexdigest()!=m['sha256']:raise ValueError('raw_hash_mismatch:'+str(raw))
        checked.append(str(raw))
    history=snapshots(root)
    if not history:raise ValueError('empty_ledger')
    s=history[-1][1]
    if s.get('execution_model','strict-open-v1')!=worker['execution_model']:raise ValueError('model_mismatch')
    reports=[]
    for directory in report_dirs:
        p=Path(directory)/'consumer-receipt.json'
        receipt=read(p) if p.exists() else None
        files=[]
        for report in sorted(Path(directory).glob('*')):
            if report.is_file() and report.suffix in ('.md','.json'):
                body=report.read_bytes();sha=hashlib.sha256(body).hexdigest()
                if receipt and receipt.get('report',{}).get('out')==str(report):
                    expected=receipt['report'].get('sha256')
                    if expected and sha!=expected:raise ValueError('report_hash_mismatch:'+str(report))
                files.append({'path':str(report),'sha256':sha,'content':body.decode('utf-8')})
        reports.append({'path':str(p),'receipt':receipt,'missing':not p.exists(),'files':files})
    return {'read_only':True,'worker':worker,'verified_raw_count':len(checked),'verified_snapshots':len(history),
            'latest_manifest':history[-1][0],'execution_model':s.get('execution_model'),
            'signals':list(s['ledger'].values()),'positions':s['positions'],'closed':s['closed'],
            'cards':s['cards'],'reports':reports,'validated':False,
            'note':'原响应/归档完整性核验不等于自然信号必出现、可成交或收益有效；核对worker时刻/状态与报告缺失项。'}
