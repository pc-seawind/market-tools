"""Only missing claim numbers=[] is schema-equivalent; argument text is sacred."""
from .ta_research import digest

DEPS={'bull_cross':('bull','bear'),'bear_cross':('bull','bear'),'C':('bull','bear','bull_cross','bear_cross')}


def normalized(value):
    if isinstance(value,list):return [normalized(x) for x in value]
    if isinstance(value,dict):
        out={k:normalized(v) for k,v in value.items()}
        if 'text' in out and 'evidence_ids' in out and 'numbers' not in out:out['numbers']=[]
        return out
    return value


def equivalent(a,b):return normalized(a)==normalized(b)


def check(role,payload,outputs):
    deps=DEPS.get(role,())
    if not deps:return [],{}
    field='debate' if role=='C' else 'initial_arguments';given=payload.get(field,{})
    errors=[];records={}
    for r in deps:
        a=given.get(r);b=outputs.get(r)
        eq=r in given and r in outputs and equivalent(a,b)
        records[r]={'request_argument_hash':digest(a),'current_output_hash':digest(b),'schema_equivalent':eq,
                    'rule':'only_missing_claim_numbers_equals_empty_list'}
        if not eq:errors.append('stale_dependency:'+r)
    return errors,records
