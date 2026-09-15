"""Conservative semantic tripwires plus separately recorded analyst review.
Passing automatic gates is never equivalent to completed source-by-source review.
"""
import re
from pathlib import Path
from .ta_research import read, digest, save, now
from .timing_cli import file_hash


def semantic_errors(output, stock):
    if not isinstance(output,dict):return ['missing_output']
    errors=[];short=output.get('short_term') or {}
    invalidation=str((output.get('thesis') or {}).get('invalidation',''))
    if re.search(r'命题(?:获得支持|得到支持|得到证实|被证实|获证实)',invalidation):
        errors.append('thesis_invalidation_supports_proposition')
    text='；'.join(str(short.get(k,'')) for k in ('scenario','trigger','invalidation'))
    if re.search(r'下季|下一季|后续季度|後續季度|下季度|季报|季報|年报|年報|半年报|半年報|月度|一至三个月|数月|數月',text):
        errors.append('cross_horizon_short_term')
    # A next-session test must be observable within that session, or unknown.
    for k in ('trigger','invalidation'):
        v=str(short.get(k,''))
        if not re.search(r'未知|无法判断|不足|未确认|开盘|收盘|盘中|停牌|公告|上市|成交|价位|报价|高点|低点',v):
            errors.append('short_term_unobservable_'+k)
    quotes=[e['content'] for e in stock['evidence'] if e['kind']=='quote']
    for c in output.get('facts',[]):
        t=c.get('text','')
        volume_claims=[v for v in re.split('[，,；;。]',t) if re.search(r'成交量放大|成交量萎缩|放量|缩量',v)]
        if any(not re.search(r'不能判断|无法判断|不能确定|无法确定|不代表|不证明',v) for v in volume_claims) and not any('previous_volume' in q or 'average_volume' in q for q in quotes):errors.append('volume_comparison_without_baseline')
        if quotes:
            q=quotes[0]
            if re.search(r'当日上涨|今日上涨|日涨幅|收涨',t) and q.get('close_vs_previous')!='higher':errors.append('wrong_day_change')
            if re.search(r'当日下跌|今日下跌|日跌幅|收跌',t) and q.get('close_vs_previous')!='lower':errors.append('wrong_day_change')
    prose=str(output)
    if re.search(r'存档[仅只].{0,4}[两二]页|归档[仅只].{0,4}[两二]页',prose):errors.append('archive_excerpt_confusion')
    for c in output.get('disagreements',[]):
        t=c.get('text','')
        if re.search(r'估值|低估',t) and re.search(r'下跌|上涨|跌幅|涨幅',t) and re.search(r'相反|背离|矛盾|证伪|反驳',t) and not re.search(r'不能|不构成|并非|无法|不代表',t):
            errors.append('price_cannot_refute_valuation')
    return sorted(set(errors))


def automatic(manifest):
    mp=Path(manifest);frozen=read(mp.parent/'input.json');result=read(mp.parent/'results.json')
    by={s['code']:s for s in frozen['stocks']};rows=[]
    for s in result['stocks']:
        role_errors={role:semantic_errors(c.get('output'),by[s['code']]) for role,c in s.get('calls',{}).items()}
        errors=[role+':'+e for role,es in role_errors.items() for e in es]
        coverage=by[s['code']].get('research_coverage')
        if coverage and coverage.get('status')!='pass':errors.append('research_coverage_blocked')
        rows.append({'code':s['code'],'errors':errors,'status':'blocked' if errors else 'review_required','roles':role_errors,
                     'result_hash':file_hash(mp.parent/s['code']/'result.json')})
    return {'run_id':result['run_id'],'manifest_hash':file_hash(mp),'input_hash':digest(frozen),'stocks':rows,'review_kind':'automatic_tripwire_not_semantic_certification'}


def checked_review(manifest, review_path):
    mp=Path(manifest);auto=automatic(mp);review=read(review_path)
    if any(review.get(k)!=auto[k] for k in ('run_id','manifest_hash','input_hash')):raise ValueError('quality_identity_mismatch')
    rows={s['code']:s for s in review['stocks']}
    stocks={s['code']:s for s in read(mp.parent/'input.json')['stocks']}
    if len(rows)!=len(review['stocks']) or set(rows)!={s['code'] for s in auto['stocks']}:raise ValueError('quality_coverage_mismatch')
    for a in auto['stocks']:
        r=rows[a['code']]
        if r.get('result_hash')!=a['result_hash']:raise ValueError('quality_result_mismatch')
        if r.get('status') not in ('pass','blocked'):raise ValueError('quality_status_invalid')
        checks=r.get('checks',{})
        if not review.get('reviewer') or not checks or any(k not in checks for k in ('facts','short_term','thesis','gaps','B_vs_C')):raise ValueError('quality_review_incomplete')
        ids={e['evidence_id'] for e in stocks[a['code']]['evidence']}
        for c in checks.values():
            if not c.get('evidence_ids') or any(e not in ids for e in c['evidence_ids']):raise ValueError('quality_review_evidence_missing')
        if r['status']=='pass' and (a['errors'] or r.get('findings') or any(c.get('status')!='pass' or not c.get('rationale') for c in checks.values())):
            raise ValueError('quality_pass_conflicts_with_findings')
    return review
