"""Traceable numeric whitelist: exact source locator + semantic dimensions.
No regex number becomes a fact without a complete reviewed metric selector.
"""
from .ta_evidence import metric_errors
from .ta_research import same_number
from pathlib import Path

DIMENSIONS=('subject','metric','period_start','period_end','scope','basis','is_forecast')


def catalog(es):
    out=[]
    for e in es:
        if e['kind'] in ('quote','financial'):
            out.append({'evidence_id':e['evidence_id'],'allowed_fields':list(e['content']),'unit':e.get('unit'),'currency':e.get('currency')})
        elif e.get('content',{}).get('numeric_metrics'):
            out.append({'evidence_id':e['evidence_id'],'metrics':e['content']['numeric_metrics'],'rule':'numbers must copy all semantic dimensions; forecasts never facts'})
    return out


def validate_number(n,e):
    if e.get('kind') in ('quote','financial'):
        expected=e.get('content',{}).get(n.get('field'));unit=e.get('unit');unit=unit.get(n.get('field')) if isinstance(unit,dict) else unit
        return (['numeric_conflict'] if expected is None or not same_number(n.get('value'),expected) else [])+(['unit_currency_conflict'] if n.get('unit')!=unit or n.get('currency')!=e.get('currency') else [])
    metric=e.get('content',{}).get('numeric_metrics',{}).get(n.get('field'))
    if not metric:return ['numeric_conflict']
    errors=[]
    try: errors+=metric_errors(metric,Path(e['text_path']).read_text(),e['kind'],e['published_at'])
    except (OSError,KeyError):errors.append('numeric_original_missing')
    if not same_number(n.get('value'),metric['value']):errors.append('numeric_conflict')
    if any(n.get(k)!=metric[k] for k in ('unit','currency')):errors.append('unit_currency_conflict')
    for k in DIMENSIONS:
        if n.get(k)!=metric[k]:errors.append('numeric_'+k+'_conflict')
    return errors


def conflicts(es):
    groups={}
    for e in es:
        for field,n in e.get('content',{}).get('numeric_metrics',{}).items():
            key=tuple(n.get(k) for k in DIMENSIONS)+ (n['unit'],n['currency'])
            groups.setdefault(key,[]).append({'evidence_id':e['evidence_id'],'field':field,'value':n['value'],'institution':e.get('original_institution')})
    return [{'dimensions':list(k),'values':vs,'status':'unresolved_source_conflict','rule':'return_to_original_do_not_average'} for k,vs in groups.items() if len({str(v['value']) for v in vs})>1]
