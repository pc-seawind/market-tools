"""Read-only, hash-bound current-vintage adapters. No historic PIT fabrication."""
import csv
import gzip
import json
from datetime import timedelta
from pathlib import Path
from .action_loop import read
from .timing import digest, instant, normalize
from .timing_cli import file_hash
from .store import digest as source_digest
from .iteration_policy import CONTRACT


def check_time(value, asof, *, age=True):
    t, cutoff = instant(value), instant(asof)
    if t > cutoff: raise ValueError('future_source')
    if age and cutoff-t > timedelta(days=CONTRACT['max_source_age_days']):
        raise ValueError('expired_source')


def retain(path, out, fetched_at, asof):
    check_time(fetched_at, asof)
    raw=Path(path).read_bytes(); h=file_hash(path)
    p=Path(out)/'sources'/(h+'.gz');p.parent.mkdir(parents=True,exist_ok=True)
    if not p.exists():
        with p.open('xb') as f:f.write(gzip.compress(raw, mtime=0))
    if gzip.decompress(p.read_bytes()) != raw:raise ValueError('archive_blob_corrupt')
    return {'original_path':str(Path(path).resolve()),'path':str(p.resolve()),'sha256':h,
            'fetched_at':fetched_at,'encoding':'gzip'}


def verify_sources(items, asof):
    import hashlib
    for m in items:
        check_time(m['fetched_at'],asof)
        raw=Path(m['path']).read_bytes()
        if m.get('encoding')=='gzip':raw=gzip.decompress(raw)
        if hashlib.sha256(raw).hexdigest()!=m['sha256']:raise ValueError('source_hash_mismatch')


def fundamental(sweep, out, asof):
    r=Path(sweep); mats=[]
    def get(name, fetched):
        p=r/name; mats.append(retain(p,out,fetched,asof));return read(p)
    sm=read(r/'summary.json');check_time(sm['updated_at'],asof)
    if sm.get('classification')!='current_snapshot_shadow_not_historical_PIT' or not sm.get('fresh_api'):
        raise ValueError('unsupported_sweep_version_or_origin')
    get('summary.json',sm['updated_at'])
    values={}
    for n in ('stock_basic','daily_basic'):
        meta=read(r/(n+'-source.json'));check_time(meta['fetched_at'],asof)
        rows=get(n+'.json',meta['fetched_at']);get(n+'-source.json',meta['fetched_at'])
        if source_digest(rows)!=meta['hash'] or not meta.get('fresh_api'):
            raise ValueError('sweep_source_hash_or_origin')
        values[n]=rows
    universe=values['stock_basic']; basics=values['daily_basic']; date=sm['asof']
    if len({s['ts_code'] for s in universe})!=len(universe) or source_digest(universe)!=sm['universe_hash']:
        raise ValueError('duplicate_or_changed_universe')
    if len({b['ts_code'] for b in basics})!=len(basics):raise ValueError('duplicate_basic')
    if any(b['trade_date']!=date.replace('-','') for b in basics):raise ValueError('daily_date_mismatch')
    check_time(date+'T15:00:00+08:00',asof)
    by={b['ts_code']:b for b in basics}; observations=[];errors=[]
    for stock in universe:
        code=stock['ts_code'];sp=r/'symbols'/(code+'.json');fp=r/('financial-'+code+'.json')
        if not sp.exists() or not fp.exists():
            errors.append({'code':code,'reason':'financial_source_missing'});continue
        meta=read(sp); check_time(meta['fetched_at'],asof)
        rows=get(fp.name,meta['fetched_at']);get('symbols/'+sp.name,meta['fetched_at'])
        if meta.get('data_hash')!=source_digest(rows) or meta['asof']!=date or meta['code']!=code:
            raise ValueError('financial_hash_date_identity_mismatch')
        if any(f.get('ts_code')!=code for f in rows):raise ValueError('foreign_financial_row')
        observations.append({'stock':stock,'daily':by.get(code,{}),'financials':rows})
    snapshot={'asof':date,'universe_size':len(universe),'observations':observations,'errors':errors,
              'historical_universe':False,'data_version':digest([universe,basics,observations]),
              'batch_codes':[s['ts_code'] for s in universe]}
    return {'snapshot':snapshot,'sources':mats,'asof':asof,'scope_codes':snapshot['batch_codes'],
            'classification':'current_snapshot_not_historical_PIT','snapshot_hash':digest(snapshot),
            'missing':errors,'universe_size':len(universe)}


def verify_fundamental(e):
    verify_sources(e['sources'],e['asof'])
    if digest(e['snapshot'])!=e['snapshot_hash']:raise ValueError('fundamental_snapshot_changed')
    # Bind computed observations to retained provider rows, not just a caller hash.
    raw={Path(m['original_path']).name:json.loads(gzip.decompress(Path(m['path']).read_bytes())) for m in e['sources']}
    universe=raw['stock_basic.json'];basic={x['ts_code']:x for x in raw['daily_basic.json']}
    if e['scope_codes']!=[x['ts_code'] for x in universe]:raise ValueError('fundamental_scope_changed')
    for o in e['snapshot']['observations']:
        code=o['stock']['ts_code']
        if o['stock'] not in universe or o['daily']!=basic.get(code,{}) or o['financials']!=raw['financial-'+code+'.json']:
            raise ValueError('fundamental_not_derived_from_source')
    return True


def technical(bundle_path, out, asof):
    b=read(bundle_path); check_time(b['asof'],asof)
    if b.get('source_kind') not in ('real_current_readonly_collection','SYNTHETIC_ONLY'):
        raise ValueError('unsupported_bundle_origin')
    if len(b['panels'])!=len(set(b['scope_codes'])) or {p['code'] for p in b['panels']}!=set(b['scope_codes']):
        raise ValueError('technical_scope_duplicate_or_missing')
    mats=[]
    for s in b['inputs']:
        if file_hash(s['path'])!=s['sha256']:raise ValueError('technical_source_changed')
        mats.append(retain(s['path'],out,s['fetched_at'],asof))
    gaps=[]
    for p in b['panels']:
        try:normalize(p,b['asof'])
        except (ValueError,KeyError,TypeError) as exc:gaps.append({'code':p['code'],'reason':str(exc)})
    return {'bundle':b,'bundle_hash':digest(b),'sources':mats,'asof':asof,'missing':gaps,
            'execution_separate':True,'execution_quote_count':len(b.get('execution_quotes',[]))}


def verify_technical(e):
    verify_sources(e['sources'],e['asof'])
    b=e['bundle']
    if digest(b)!=e['bundle_hash']:raise ValueError('technical_bundle_changed')
    if b.get('source_kind')=='SYNTHETIC_ONLY':return True
    # Reconstruct CN OHLCV/factors directly from exact provider CLI stdout.
    sources={s['sha256']:m for s,m in zip(b['inputs'],e['sources'])}
    for p in b['panels']:
        if p['market']!='CN' or not p.get('bars'):continue
        def rows(api):
            found=[s for s in b['inputs'] if s.get('api')==api and s.get('params',{}).get('ts_code')==p['code']]
            if len(found)!=1:raise ValueError('raw_price_adapter_source_missing')
            m=sources[found[0]['sha256']]
            return list(csv.DictReader(gzip.decompress(Path(m['path']).read_bytes()).decode().splitlines()))
        prices={x['trade_date']:x for x in rows('daily')}; factors={x['trade_date']:x for x in rows('adj_factor')}
        for bar in p['bars']:
            d=bar['date'].replace('-','')
            if any(float(prices[d][k])!=bar[k] for k in ('open','high','low','close','vol')) or float(factors[d]['adj_factor'])!=bar['factor']:
                raise ValueError('technical_panel_not_from_raw')
    # Reconstruct HK factor/raw-day adapter too; qfqday is never accepted as raw.
    import ast
    import bisect
    for p in b['panels']:
        if p['market']!='HK' or p.get('adjustment')!='vendor_factor_verified':continue
        code=p['code'].split('.')[0]
        def body(part):
            found=[x for x in b['inputs'] if part in x.get('url','')]
            if len(found)!=1:raise ValueError('HK_source_ambiguous_or_missing')
            m=sources[found[0]['sha256']]
            return gzip.decompress(Path(m['path']).read_bytes()).decode()
        raw=json.loads(body('param=hk'+code+',day,,,200,'))['data']['hk'+code]['day']
        factors=ast.literal_eval(body('/'+code+'/qfq.js').split('=',1)[1].split('\n',1)[0].rstrip(';'))['data']
        ff=sorted((x['d'],float(x['f'])) for x in factors); dates=[x[0] for x in ff]
        by={r[0]:r for r in raw}
        for bar in p['bars']:
            r=by[bar['date']]; i=bisect.bisect_right(dates,bar['date'])-1
            if i<0 or [bar[k] for k in ('open','close','high','low','vol')]!=[float(v) for v in r[1:6]] or bar['factor']!=ff[i][1]:
                raise ValueError('HK_panel_not_from_raw')
    return True


def collect_marks(out, date, asof=None):
    """Actually attempt supplementation. Preserve raw stdout + bounded failure receipts."""
    import os
    import subprocess
    import sys
    from .action_loop import now
    from .data import atomic_json, HERE
    root=Path(out);root.mkdir(parents=True,exist_ok=True);records={}
    for api in ('daily','adj_factor'):
        p=root/(api+'.csv');meta=root/(api+'.receipt.json')
        if meta.exists() and p.exists():
            m=read(meta)
            if file_hash(p)!=m['sha256']:raise ValueError('supplement_changed')
            records[api]=m;continue
        started=now();cmd=[sys.executable,str(HERE/'tushare.py'),api,'--csv','trade_date='+date.replace('-','')]
        try:
            cp=subprocess.run(cmd,capture_output=True,timeout=45,env={**os.environ,'TUSHARE_NO_CACHE':'1','TUSHARE_NO_PARQUET':'1'})
            raw=cp.stdout;rc=cp.returncode;error=None if not rc else 'provider_exit_'+str(rc)
        except subprocess.TimeoutExpired:
            raw=b'';rc=124;error='provider_timeout_45s'
        p.write_bytes(raw)
        m={'command':cmd,'started_at':started,'fetched_at':now(),'returncode':rc,'error':error,
           'sha256':file_hash(p),'path':str(p.resolve()),'api':api,'date':date}
        atomic_json(meta,m);records[api]=m
    return {'records':records,'date':date,'not_PIT_or_execution':True}


def mark_rows(marks, asof):
    rows={}
    for api,m in marks['records'].items():
        check_time(m['fetched_at'],asof)
        if file_hash(m['path'])!=m['sha256']:raise ValueError('mark_source_hash')
        if m['returncode']!=0:return [],['supplement_'+api+'_failed']
        rows[api]=list(csv.DictReader(Path(m['path']).read_text().splitlines()))
        if len({r['ts_code'] for r in rows[api]})!=len(rows[api]):raise ValueError('duplicate_mark_code')
    factors={r['ts_code']:r for r in rows['adj_factor']};result=[]
    for r in rows['daily']:
        if r['ts_code'] not in factors:continue
        f=factors[r['ts_code']]
        if r['trade_date']!=marks['date'].replace('-','') or f['trade_date']!=r['trade_date']:
            raise ValueError('mark_date_mismatch')
        result.append({'code':r['ts_code'],'price':float(r['close'])*float(f['adj_factor']),
                       'date':marks['date'],'close_at':marks['date']+'T15:00:00+08:00','market':'CN',
                       'basis':'tushare_daily_times_adj_factor_constant_unit_current_vintage'})
    return result, [] if result else ['empty_daily_marks']
