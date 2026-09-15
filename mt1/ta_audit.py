"""Independent readback of request/response/input identities, not model self-score."""
import json
import re
from pathlib import Path
from .ta_research import read, digest, validate_output, evidence_errors, verify_manifest, ROLES
from .timing import instant
from .timing_cli import file_hash
from .calendar import gate, review_deadline, research_snapshot


def strict_output(o, es):
    errors=validate_output(o,es)
    if not isinstance(o,dict):return errors
    root={'facts','bull_case','bear_case','disagreements','adjudication','gaps','held_direction','unheld_direction','short_term','thesis','next_review_date'}
    if set(o)!=root:errors.append('unexpected_output_fields')
    for field in ('facts','bull_case','bear_case','disagreements','adjudication'):
        claims=[o.get(field)] if field=='adjudication' else o.get(field,[])
        if not isinstance(claims,list):errors.append('invalid_claim_schema');continue
        for c in claims:
            if not isinstance(c,dict) or set(c)!={'text','evidence_ids','numbers'}:errors.append('invalid_claim_schema');continue
            if not isinstance(c['text'],str) or not isinstance(c['numbers'],list) or not isinstance(c['evidence_ids'],list):errors.append('invalid_claim_type')
            if c['text'] and not c['evidence_ids']:errors.append('unreferenced_claim')
    for field in ('held_direction','unheld_direction','next_review_date'):
        if not isinstance(o.get(field),str):errors.append('invalid_direction_date_type')
    if not isinstance(o.get('gaps'),list) or any(not isinstance(x,str) for x in o.get('gaps',[])):errors.append('invalid_gaps')
    return sorted(set(errors))


def audit(manifest):
    mp=Path(manifest);m=verify_manifest(mp);root=mp.parent;frozen=read(root/'input.json');results=read(root/'results.json')
    if digest(frozen)!=m['input_hash']:raise ValueError('frozen_hash_mismatch')
    if results['run_id']!=m['run_id']:raise ValueError('run_identity_mismatch')
    stocks={s['code']:s for s in frozen['stocks']}
    outputs={code:{r:read(root/code/(r+'.json')).get('output') for r in ROLES if (root/code/(r+'.json')).exists()} for code in stocks}
    if len(stocks)!=len(frozen['stocks']) or set(stocks)!={s['code'] for s in results['stocks']}:raise ValueError('coverage_identity')
    report={'run_id':m['run_id'],'input_hash':m['input_hash'],'manifest_hash':file_hash(mp),'stocks':[],
            'semantic_fact_verification':'not_fully_automated; independent_human_review_required','A_comparability':'non_strict_prior_real_report','usage':{'prompt_tokens':0,'completion_tokens':0,'calls':0,'elapsed_seconds_sum':0},'USD_cost':None}
    for code,stock in stocks.items():
        if not outputs[code]:
            report['stocks'].append({'code':code,'status':'blocked','target_session':stock.get('target_session'),'errors':['model_not_run'],'roles':{}});continue
        row={'code':code,'target_session':stock['target_session'],'target_session_owner':'frozen_exchange_calendar_not_LLM','roles':{},'errors':[],'company_original':any(e['kind']=='company_primary' for e in stock['evidence'])}
        for e in stock['evidence']:
            row['errors']+=evidence_errors(e,frozen['as_of'])
            if frozen.get('quality_contract') and e['kind']=='quote':
                from .ta_research import research_quote
                if research_quote(Path(e['raw_path']).read_bytes(),code)!=e['content']:row['errors'].append('quote_parse_recompute_mismatch')
        try:
            quote=next(e for e in stock['evidence'] if e['kind']=='quote')
            raw=Path(quote['raw_path']).parent
            cp=raw/('inputs/calendar_HK.json' if stock['market']=='HK' else 'inputs/calendar.json')
            cal=read(cp)
            if quote.get('calendar_phase')=='research':
                cal=research_snapshot(cal,read(raw/('inputs/calendar-next-'+stock['market']+'.json')),stock['market'])
            cg=gate(cal,stock['market'],quote.get('calendar_phase','evening'),instant(quote['fetched_at']))
            if cg!=quote['calendar_gate']:row['errors'].append('calendar_recompute_mismatch')
            np=raw/('inputs/calendar-next-'+stock['market']+'.json');nc=read(np)
            ds=[dict(zip(nc['data']['fields'],x)) for x in nc['data']['items']]
            target=stock['target_session']
            if not target or not any(x['cal_date']==target.replace('-','') and str(x['is_open'])=='1' for x in ds):row['errors'].append('target_calendar_missing')
            row['calendar_sources']=[{'path':str(p),'sha256':file_hash(p)} for p in (cp,np)]
        except (OSError,ValueError,KeyError,StopIteration):row['errors'].append('calendar_evidence_missing')
        for role in ROLES:
            try:
                req=read(root/code/(role+'.request.json'));response=read(root/code/(role+'.response.json'));receipt=read(root/code/(role+'.json'))
                payload=json.loads(req['messages'][-1]['content'])
                errors=[]
                if digest(req)!=receipt['request_hash']:errors.append('request_hash_mismatch')
                if payload['frozen_input_hash']!=digest(frozen) or payload['evidence']!=stock['evidence']:errors.append('different_frozen_inputs')
                if any(req.get(k)!=v for k,v in frozen['model'].items()):errors.append('different_model_parameters')
                if payload['role']!=role or payload['stock']['code']!=code:errors.append('role_stock_mismatch')
                o=receipt['output']
                parsed=json.loads(response['choices'][0]['message']['content'])
                if o!=parsed:errors.append('parsed_response_changed')
                if response['model']!=frozen['model']['model']:errors.append('returned_model_mismatch')
                if response['choices'][0]['finish_reason']!='stop':errors.append('incomplete_response')
                from .ta_dependencies import check
                dep_errors,dep_hashes=check(role,payload,outputs[code]);errors+=dep_errors
                errors+=strict_output(o,stock['evidence'])
                if o['short_term']['target_date'] not in (None,stock['target_session']):errors.append('target_session_mismatch')
                if review_deadline(o['next_review_date'],stock['market'])<=instant(frozen['as_of']):errors.append('review_not_future')
                u=response['usage']
                candidate_dirs=receipt.get('inference_candidates')
                candidate_receipts=[]
                for candidate in candidate_dirs or []:
                    base=root/code/candidate;cr=read(base/(role+'.json'));cq=read(base/(role+'.request.json'));cs=read(base/(role+'.response.json'))
                    cp=json.loads(cq['messages'][-1]['content'])
                    if digest(cq)!=cr['request_hash'] or file_hash(base/(role+'.response.json'))!=cr['response_hash']:errors.append('candidate_hash_mismatch')
                    if cp['evidence']!=stock['evidence'] or cp['frozen_input_hash']!=digest(frozen) or cp['role']!=role:errors.append('candidate_identity_mismatch')
                    if any(cq.get(k)!=v for k,v in frozen['model'].items()):errors.append('candidate_model_mismatch')
                    candidate_receipts.append(cr)
                for cr in candidate_receipts or [receipt]:
                    cu=cr['usage'];report['usage']['prompt_tokens']+=cu.get('prompt_tokens',0);report['usage']['completion_tokens']+=cu.get('completion_tokens',0)
                    report['usage']['calls']+=sum(1 for a in cr['attempts'] if a['status']==200)
                    report['usage']['elapsed_seconds_sum']+=sum(a['elapsed_seconds'] for a in cr['attempts'])
                row['roles'][role]={'errors':sorted(set(errors)),'request_hash':receipt['request_hash'],'provider_id':response['id'],'usage':u,'dependencies':dep_hashes}
                row['errors'] += [role+':'+e for e in errors]
            except (KeyError,ValueError,TypeError,OSError) as e:row['errors'].append(role+':'+type(e).__name__)
        row['status']='blocked' if row['errors'] else 'pass'
        report['stocks'].append(row)
    report['usage']=model_usage(mp)
    return report


def model_usage(manifest):
    """Count failed/invalid-JSON inferences too, before semantic parsing can fail.
    Candidate receipts are canonical cost owners; canonical file copies never
    double count. A targeted revision separates inherited from new calls.
    """
    mp=Path(manifest);result=read(mp.parent/'results.json')
    out={'prompt_tokens':0,'completion_tokens':0,'calls':0,'elapsed_seconds_sum':0,
         'network_failed_attempts':0,'new_elapsed_seconds_sum':0,'new_calls':0,'new_prompt_tokens':0,'new_completion_tokens':0,
         'cost_amount':None,'cost_reason':'no_verified_billing_rate_not_inferred'}
    for stock in result['stocks']:
        for role,canonical in stock.get('calls',{}).items():
            dirs=canonical.get('inference_candidates')
            receipts=[read(mp.parent/stock['code']/d/(role+'.json')) for d in dirs] if dirs else [canonical]
            inherited=stock.get('unchanged_roles') and role not in stock.get('changed_roles',[])
            for receipt in receipts:
                u=receipt.get('usage',{});attempts=receipt.get('attempts',[])
                calls=sum(a.get('status')==200 for a in attempts)
                for key in ('prompt_tokens','completion_tokens'):out[key]+=u.get(key,0)
                out['calls']+=calls;out['elapsed_seconds_sum']+=sum(a.get('elapsed_seconds',0) for a in attempts)
                out['network_failed_attempts']+=sum(a.get('status')!=200 for a in attempts)
                if not inherited:
                    out['new_calls']+=calls
                    out['new_elapsed_seconds_sum']+=sum(a.get('elapsed_seconds',0) for a in attempts)
                    for key in ('prompt_tokens','completion_tokens'):out['new_'+key]+=u.get(key,0)
    return out
