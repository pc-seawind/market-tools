"""Read-only research reader used by actual daily/weekly report consumers.
Never modifies original company section or technical action. Missing/corrupt/stale
research returns a visible gap, not an exception that blocks the base report.
"""
from pathlib import Path
import os
from datetime import timedelta
from .ta_research import DEFAULT_ROOT, read, verify_manifest, SCOPE, digest, evidence_errors
from .timing import instant
from .timing_cli import file_hash


def consume(asof, root=None, scope_path=None):
    root=Path(root or os.environ.get("TA10_RESEARCH_ROOT") or DEFAULT_ROOT);scope_path=Path(scope_path or SCOPE)
    result={'status':'research_unavailable','not_published':True,'coverage':[],
            'technical_actions_unchanged':True,'semantic_acceptance':False}
    try:
        pointer=read(root/'latest.json');mp=Path(pointer['manifest'])
        if file_hash(mp)!=pointer['sha256']:raise ValueError('manifest_hash_mismatch')
        m=verify_manifest(mp)
        from .ta_audit import audit
        audited=audit(mp);audit_by={s['code']:s for s in audited['stocks']}
        frozen=read(mp.parent/'input.json');r=read(mp.parent/'results.json')
        if digest(frozen)!=m['input_hash']:raise ValueError('input_hash_mismatch')
        if file_hash(scope_path)!=frozen['scope_hash']:raise ValueError('scope_changed')
        if not timedelta(0)<=instant(asof)-instant(r['as_of'])<=timedelta(days=3):raise ValueError('stale_or_future_research')
        for s in frozen['stocks']:
            for e in s['evidence']:
                if evidence_errors(e,frozen['as_of']):raise ValueError('evidence_integrity')
        result.update(status='research_consumed',run_id=r['run_id'],manifest_hash=pointer['sha256'],input_hash=m['input_hash'],scope_epoch=r['scope_epoch'])
        from .ta_quality import automatic, checked_review
        auto=automatic(mp);auto_by={s['code']:s for s in auto['stocks']}
        quality_path=Path(pointer['quality_review']) if pointer.get('quality_review') else None
        quality=None
        if quality_path:
            if file_hash(quality_path)!=pointer.get('quality_hash'):raise ValueError('quality_hash_mismatch')
            quality=checked_review(mp,quality_path)
        quality_by={s['code']:s for s in quality['stocks']} if quality else {}
        result['quality_review_hash']=pointer.get('quality_hash')
        result['quality_review_kind']=quality.get('review_kind') if quality else 'not_yet_reviewed'
        lines=['🧪 **公司研究复核｜TA-1.0，独立于技术动作**','首轮仅为研究机制验证，不是公司签审或收益认证；短期目标交易日见冻结输入，短期及长期观察均未成熟。',
               '|标的|协议检查|持仓研究方向|未持有研究方向|分歧/缺口|','|---|---|---|---|---|']
        for s in r['stocks']:
            frozen_stock=next(x for x in frozen['stocks'] if x['code']==s['code'])
            research_coverage=frozen_stock.get('research_coverage',{'status':'not_searched','reason':'legacy_run_before_active_collection'})
            output=s.get('calls',{}).get('C',{}).get('output') or {}
            audit_row=audit_by[s['code']]
            q=quality_by.get(s['code'],{})
            quality_errors=auto_by[s['code']]['errors']+([] if q.get('status')=='pass' else ['semantic_review_missing_or_blocked'])
            if research_coverage.get('status')!='pass':quality_errors.append('research_coverage_'+research_coverage.get('status','unknown'))
            valid=not quality_errors and audit_row['status']=='pass' and not [e for e in s.get('blockers',[]) if not e.endswith((':numeric_conflict',':target_session_mismatch',':volume_comparison_without_baseline'))]
            result['coverage'].append({'code':s['code'],'research_coverage':research_coverage,'research_coverage_hash':digest(research_coverage),'target_session':audit_row['target_session'],'status':'pass' if valid else 'blocked','audit_errors':audit_row['errors'],'result_hash':file_hash(mp.parent/s['code']/'result.json'),'original_gate_errors':s.get('blockers',[]),'recomputed_gate_note':'original errors retained; numeric/date and explicit volume-negation false positives only resolved by fresh audit + automatic semantic gate + hash-bound analyst review','blockers':audit_row['errors']+quality_errors,'semantic_quality':q or {'status':'pending'},'automatic_quality':auto_by[s['code']]})
            # Failed model output is archived but never displayed as validated prose.
            held=output.get('held_direction','未知') if valid else '未知，待补证与复核'
            unheld=output.get('unheld_direction','未知') if valid else '暂不形成新研究结论'
            gaps='；'.join(output.get('gaps',[])[:2]) if valid else '；'.join(s.get('blockers',[])+audit_row['errors']+quality_errors)
            clean=lambda x:str(x).replace('|','／').replace('\n',' ')
            lines.append('|'+ '|'.join(map(clean,[s['name']+' '+s['code'],'pass' if valid else 'blocked',held,unheld,gaps]))+'|')
        lines+=['','研究run_id：'+r['run_id'],'归档hash：'+pointer['sha256'],'逐股语义复核见结构化quality回执，最终仍待独立验收；不计算个人盈亏，不将本轮计入正式荐股成功率。']
        result['markdown']='\n'.join(lines)+'\n'
    except Exception as e:
        result.update(status='research_unavailable',coverage=[],error_type=type(e).__name__,reason=str(e),markdown='🧪 **公司研究复核缺口**：本轮无可校验且新鲜的研究包；行情、原公司研究与技术动作照常保留。\n')
    return result
