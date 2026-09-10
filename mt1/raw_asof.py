"""Raw source -> five typed *draft* inputs, with field/source lineage.

No self-reported PIT boolean can admit data. Provider snapshots taken now cannot
prove their historic versions. This stage never emits verified envelopes; the
existing historical.recompute gate remains separate and fails closed. Drafts
are useful for explicit mapping/coverage review, never investment conclusions.
"""
import json
from dataclasses import fields
from pathlib import Path
from zoneinfo import ZoneInfo
from .historical import day,instant,rules_version,recompute,price_features
from .evidence import file_ref
from .store import digest


def raw_sources(summary_path):
    summary=json.loads(Path(summary_path).read_text());result=[]
    for item in summary['results']:
        if not item.get('artifact'):continue
        ref=item['artifact']
        if file_ref(ref['path'])['sha256']!=ref['sha256']:raise ValueError('raw source hash mismatch')
        rows=json.loads(Path(ref['path']).read_text())
        result.append({'api':item['task']['api'],'params':item['task']['params'],
            'source':ref,'fetched_at':item['fetched_at'],'rows':rows})
    return result


def finite(value):
    import math
    if value in (None,''):return None
    n=float(value)
    if not math.isfinite(n):raise ValueError('nonfinite raw value')
    return n


def select_diagnostic(rows,code,cutoff,date_field):
    """Discard future rows only for a labelled diagnostic, not PIT admission."""
    eligible=[];unknown=[];future=[]
    for row in rows:
        if row.get('ts_code')!=code:continue
        value=row.get(date_field)
        if not value:unknown.append(row);continue
        if day(value)>cutoff:future.append(row)
        else:eligible.append(row)
    return eligible,unknown,future


def action_evidence(rows,code,start,end,delist_date=None):
    """Discover events; do not invent cash/tax/reinvestment/settlement treatment."""
    events=[];rejected=[]
    for row in rows:
        if row.get('ts_code')!=code:continue
        ex=row.get('ex_date')
        if ex and not day(start)<=day(ex)<=day(end):continue
        if not ex:
            # Incomplete in-window announcements are unresolved, not zero cash.
            ann=row.get('imp_ann_date') or row.get('ann_date')
            if ann and not day(start)<=day(ann)<=day(end):continue
        reasons=[]
        if row.get('div_proc')!='实施':reasons.append('not_implemented')
        for k in ('ex_date','record_date','pay_date','cash_div_tax','stk_div'):
            if row.get(k) in ('',None):reasons.append('missing_'+k)
        if not reasons:
            try:
                if day(row['record_date'])>day(row['ex_date']) or day(row['pay_date'])<day(row['ex_date']):reasons.append('invalid_event_dates')
                if finite(row['cash_div_tax'])<0 or finite(row['stk_div'])<0:reasons.append('negative_action_amount')
                if finite(row['stk_div'])>0 and not row.get('div_listdate'):reasons.append('stock_credit_date_unknown')
            except (ValueError,TypeError):reasons.append('invalid_action_values')
        record={'raw':row,'raw_row_hash':digest(row),'version_status':'unknown'}
        (rejected if reasons else events).append({**record,'reasons':reasons} if reasons else record)
    blockers=['corporate_action_version_chain_unknown','event_coverage_not_certified',
              'tax_and_cash_reinvestment_policy_not_audited','cash_stock_ledger_not_reconciled']
    if delist_date and day(delist_date)<=day(end):blockers.append('delisting_settlement_unknown_no_last_price_or_zero_substitution')
    return {'events':events,'rejected_events':rejected,'blockers':blockers,'replay_ready':False,
            'settlement':None,'metrics':None}


def build_draft(code,name,decision_at,sessions,union,sources,delist_date=None):
    from sector_picks import StockMetrics
    from etf_data import SectorSignals
    cutoff=instant(decision_at).astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat()
    by_api={};refs={}
    for s in sources:
        by_api.setdefault(s['api'],[]).extend(s['rows'])
        refs.setdefault(s['api'],[]).append({k:s[k] for k in ('source','params','fetched_at')})
    draft={k:{'payload':{},'blockers':[],'lineage':{}} for k in ('stock','financial','membership','sector','context')}
    stock=draft['stock'];stock['payload'].update(code=code,name=name)
    ds=[d for d in sessions if d<=cutoff][-250:]
    try:
        panel={a:[] for a in ('daily','adj_factor')}
        for d in ds:
            for api in panel:
                row,err=union.get(api,code,d)
                if err:raise ValueError(f'{api}:{d}:{err}')
                panel[api].append(row)
        stock['payload'].update(price_features(code,decision_at,ds,panel['daily'],panel['adj_factor']))
        stock['lineage']['technical']={'recipe':'mt1.historical.price_features','input_hash':digest(panel),'sessions':ds,'provenance':'union.sqlite provenance by (api,code,day)'}
    except ValueError as e:stock['blockers'].append(str(e))
    vals,unknown,future=select_diagnostic(by_api.get('daily_basic',[]),code,cutoff,'trade_date')
    vals=[r for r in vals if day(r['trade_date'])==cutoff]
    if vals and len({digest(r) for r in vals})==1:
        r=vals[0]
        for dest,src in {'pe_ttm':'pe_ttm','pb':'pb','turnover':'turnover_rate','mkt_cap_yi':'total_mv'}.items():
            n=finite(r.get(src))
            if n is not None:stock['payload'][dest]=n/10000 if dest=='mkt_cap_yi' else n
        stock['lineage']['valuation']={'api':'daily_basic','raw_row_hash':digest(r)}
    else:stock['blockers'].append('decision_day_valuation_missing_or_conflicting')
    stock['blockers']+=['price_and_valuation_historical_versions_unattested','three_year_PE_history_not_certified']
    financial=draft['financial']
    eligible,unknown,future=select_diagnostic(by_api.get('fina_indicator',[]),code,cutoff,'ann_date')
    financial['lineage']={'eligible_row_hashes':[digest(r) for r in eligible],
        'unknown_announcement_rows':len(unknown),'excluded_future_rows':len(future),
        'income_rows':sum(r.get('ts_code')==code for r in by_api.get('income',[]))}
    # Do not pick a current revised report to populate an asof payload. Expose
    # raw mapping candidates separately, preserving every eligible alternative.
    financial['mapping_candidates']=[{'source_row_hash':digest(r),'fina_as_of':r.get('end_date'),
        **{dest:finite(r.get(src)) for dest,src in {'roe':'roe','gross_margin':'grossprofit_margin','net_yoy':'netprofit_yoy','rev_yoy':'or_yoy'}.items()}} for r in eligible]
    financial['blockers']=['original_release_and_revision_chain_unknown','ann_date_or_update_flag_is_not_version_certification']
    members=[]
    for r in by_api.get('index_member_all',[]):
        if r.get('ts_code')==code and r.get('in_date') and day(r['in_date'])<=cutoff and (not r.get('out_date') or cutoff<day(r['out_date'])):members.append(r)
    draft['membership'].update(mapping_candidates=members,blockers=['Shenwan_intervals_are_not_production_concept_history','historical_membership_source_completeness_unknown'])
    draft['sector']['blockers']=['historical_direct_ETF_price_flow_and_weights_missing'];draft['sector']['required_fields']=[f.name for f in fields(SectorSignals)]
    draft['context']['blockers']=['historical_sector_score_and_context_missing']
    stock['missing_fields']=sorted({f.name for f in fields(StockMetrics)}-set(stock['payload']))
    # No envelope is emitted based on user/provider-supplied pit_verified=True.
    bundle={'decision_at':decision_at,'rule_version':rules_version(),'inputs':{}}
    return {'code':code,'decision_at':decision_at,'status':'drafts_built_admission_blocked',
        'interpretation':'retrospective_current_rule_mapping_not_2024_active_rule',
        'draft_inputs':draft,'raw_sources':refs,'draft_hash':digest(draft),
        'bundle':bundle,'recompute':recompute(bundle),
        'actions':action_evidence(by_api.get('dividend',[]),code,'2024-01-02','2024-09-30',delist_date),
        'replay_ready':False,'metrics':None}
