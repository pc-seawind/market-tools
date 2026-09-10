"""Retrospective missing-row explanation, NOT historical trading permission."""
import hashlib,re
from pathlib import Path
from .historical import day


def checked_text(ref):
    raw=Path(ref['path']).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=ref['sha256']:raise ValueError('evidence hash mismatch')
    return raw.decode('utf-8')


def validate_interval(evidence):
    text=checked_text(evidence['text']);checked_text(evidence['mapping_text'])
    # PDF bytes and extracted text are separately frozen; expected interval
    # markers must occur literally in the supplied issuer text.
    raw=Path(evidence['pdf']['path']).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=evidence['pdf']['sha256'] or not raw.startswith(b'%PDF'):raise ValueError('PDF mismatch')
    mapping=checked_text(evidence['mapping_text'])
    old=evidence['old_code'];new=evidence['code'].split('.')[0]
    if not re.search(r'\b'+old+r'\s+'+new+r'\b',mapping):raise ValueError('unproven code alias')
    compact=re.sub(r'\s+','',text)
    compact=re.sub(r'[（(][^）)]*[）)]','',compact)
    if old not in compact:raise ValueError('issuer identity missing')
    start=day(evidence['start']);end=day(evidence['resume'])
    def chinese(d):return f'{int(d[:4])}年{int(d[5:7])}月{int(d[8:])}日'
    if chinese(start)+'起停牌' not in compact:raise ValueError('suspension start not in document')
    # Exact actual-resumption phrase, never "预计...日前复牌".
    if re.search(r'(预计|拟|计划)[^。；]{0,30}'+chinese(end),compact):raise ValueError('actual resumption not established')
    if not any(chinese(end)+s in compact for s in ('起复牌','开市起复牌')):raise ValueError('actual resumption not in document')
    if start>=end:raise ValueError('invalid interval')
    return start,end


def explain(rows,evidences):
    intervals={e['code']:(*validate_interval(e),e) for e in evidences}
    result=[]
    for r in rows:
        x=intervals.get(r['code']);covered=x and x[0]<=r['day']<x[1]
        result.append({**r,'explanation':'issuer_confirmed_suspension' if covered else 'still_requires_resumption_evidence',
            'evidence':x[2] if covered else None,'daily_row_filled':False,'tradable':False,
            'PIT_permission':False,'note':'retrospective missing-row explanation only'})
    return result
