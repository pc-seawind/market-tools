"""MT14 isolated historical inputs. Actual response bytes, bounded requests, no live writes."""
import gzip
import hashlib
import json
import os
from pathlib import Path
from datetime import datetime, timezone
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def read(path):
    p = Path(path)
    return json.loads(gzip.decompress(p.read_bytes()) if p.suffix == '.gz' else p.read_bytes())


def write(path, value):
    """Immutable/idempotent files. Mismatch is an error, not overwrite."""
    p = Path(path); p.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, indent=2)+'\n').encode()
    if p.exists():
        if p.read_bytes() != raw:
            raise ValueError('immutable_file_changed:'+str(p))
    else:
        with p.open('xb') as f: f.write(raw)


def seal_bytes(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != raw: raise ValueError('source_archive_changed')
    else: path.write_bytes(raw)


class Collector:
    """One attempt per frozen request, including errors. Explicit denials stop endpoint."""
    def __init__(self, out, offline=False):
        self.out=Path(out); self.offline=offline; self.denied={}

    def get(self, api, **params):
        req=dict(api_name=api,params=params,fields='')
        key=hashlib.sha256(json.dumps(req,sort_keys=True).encode()).hexdigest()[:24]
        meta=self.out/'raw'/(api+'-'+key+'.meta.json');body=meta.with_name(meta.name.replace('.meta.json','.raw'))
        if meta.exists():
            m=read(meta)
            if m['request']!=req or (m.get('sha256') and sha(body)!=m['sha256']): raise ValueError('raw_tamper')
        else:
            if self.offline: raise ValueError('offline_missing:'+api+':'+key)
            m=dict(request=req,acquired_at=datetime.now(timezone.utc).isoformat(),attempts=1,endpoint='https://api.tushare.pro')
            raw=None
            try:
                if api in self.denied: raise ValueError('endpoint_denied_previous_request:'+self.denied[api])
                token=os.environ.get('TUSHARE_TOKEN')
                if not token: raise ValueError('credential_missing:TUSHARE_TOKEN')
                q=urllib.request.Request(m['endpoint'],data=json.dumps({**req,'token':token}).encode(),headers={'Content-Type':'application/json'})
                with urllib.request.urlopen(q,timeout=25) as response: raw=response.read()
                d=json.loads(raw)
                if d.get('code')!=0: raise ValueError('provider_error:'+str(d.get('code'))+':'+str(d.get('msg')))
                if not d.get('data'): raise ValueError('provider_empty_data')
                n=len(d['data']['items'])
                if n>=5000 or (api=='fina_indicator' and n>=100): raise ValueError('possible_truncation_requires_split')
                m.update(status='ok',rows=n)
            except Exception as e:
                # HTTPError bodies are evidence as well. Never log the token/request body.
                if raw is None and hasattr(e,'read'): raw=e.read()
                msg=str(e).replace(os.environ.get('TUSHARE_TOKEN','__NO_TOKEN__'),'[REDACTED]')
                m.update(status='failed',error=msg)
            if raw is not None:
                seal_bytes(body,raw);m.update(sha256=sha(body),body=str(body.relative_to(self.out)))
            write(meta,m)
        if m['status']!='ok':
            if any(x in m['error'] for x in ('权限','没有访问','无权限','permission')): self.denied[api]=m['error']
            return [],dict(path=str(meta.relative_to(self.out)),**m)
        d=read(body)['data']
        return [dict(zip(d['fields'],r)) for r in d['items']],dict(path=str(meta.relative_to(self.out)),**m)


def archive_sources(out,cfg):
    """Copy source panels and their raw provenance; does not import any old returns."""
    refs=[]
    for spec in cfg['panels']:
        p=ROOT/spec['source']
        if sha(p)!=spec['sha256']: raise ValueError('frozen_panel_changed')
        panel=read(p)
        targets=[(p,spec['sha256'])]
        for src in panel['sources']:
            origin=(p.parent/src['path']) if src['path'].startswith('raw/') else ROOT/src['path']
            targets.append((origin,src['sha256']))
            meta=origin.with_suffix('.meta.json')
            if meta.exists(): targets.append((meta,sha(meta)))
        for origin,h in targets:
            if sha(origin)!=h: raise ValueError('old_raw_source_changed:'+str(origin))
            dest=out/'inputs'/ (h+'.gz')
            seal_bytes(dest,gzip.compress(origin.read_bytes(),mtime=0))
            refs.append(dict(origin=str(origin.relative_to(ROOT)),path=str(dest.relative_to(out)),sha256=sha(dest),uncompressed_sha256=h))
    write(out/'source-index.json',refs)
    return refs


def collect(out,offline=False):
    out=Path(out);cfg=read(out/'frozen-contract.json');archive_sources(out,cfg)
    c=Collector(out,offline);records=[];fund={};warm={}
    cal,rec=c.get('trade_cal',exchange='SSE',start_date='20200601',end_date='20201231');records.append(rec)
    warm['dates']=sorted(r['cal_date'] for r in cal if int(r['is_open'])==1)
    for code in cfg['fundamental_codes']:
        finance=[];basic=[];names=[]
        for year in range(2019,2026):
            rows,r=c.get('fina_indicator',ts_code=code,start_date=f'{year}0101',end_date=f'{year}1231');records.append(r);finance+=rows
        for year in range(2021,2026):
            rows,r=c.get('daily_basic',ts_code=code,start_date=f'{year}0101',end_date=f'{year}1231');records.append(r);basic+=rows
        rows,r=c.get('namechange',ts_code=code,start_date='20190101',end_date='20251231');records.append(r);names=rows
        fund[code]=dict(financials=finance,daily=basic,names=names,financial_vintage='current_vendor_revision_not_original_PIT')
        w={}
        for api in ('daily','adj_factor'):
            rows,r=c.get(api,ts_code=code,start_date='20200601',end_date='20201231');records.append(r);w[api]=rows
        warm[code]=w
        write(out/'checkpoints'/(code+'.json'),dict(financial=fund[code],warmup=w))
    # Bounded feasibility probes for missing user holdings/HK prefix. No paid endpoint retry.
    for api,params in [('hk_daily',dict(ts_code='00005.HK',start_date='20200601',end_date='20201231')),
                       ('hk_adjfactor',dict(ts_code='00005.HK',start_date='20200601',end_date='20201231')),
                       ('daily',dict(ts_code='001309.SZ',start_date='20210101',end_date='20211231')),
                       ('stock_basic',dict(ts_code='001309.SZ',list_status='L')),
                       ('stock_basic',dict(list_status='D'))]:
        _,r=c.get(api,**params);records.append(r)
    write(out/'financial-data.json',fund);write(out/'warmup-data.json',warm);write(out/'collection-receipt.json',records)
    index={str(p.relative_to(out)):sha(p) for d in ('raw','inputs','checkpoints') for p in sorted((out/d).glob('*'))}
    for n in ('source-index.json','financial-data.json','warmup-data.json','collection-receipt.json','frozen-contract.json','inventory.json'):
        index[n]=sha(out/n)
    write(out/'input-manifest.json',index)
    return dict(requests=len(records),ok=sum(r['status']=='ok' for r in records),failed=sum(r['status']!='ok' for r in records))


def verify_inputs(out):
    out=Path(out);index=read(out/'input-manifest.json')
    for name,h in index.items():
        if sha(out/name)!=h: raise ValueError('input_hash_mismatch:'+name)
    for r in read(out/'source-index.json'):
        raw=gzip.decompress((out/r['path']).read_bytes())
        if hashlib.sha256(raw).hexdigest()!=r['uncompressed_sha256']: raise ValueError('source_raw_hash_mismatch')
    return index


def panels(out,cfg):
    lookup={r['origin']:r for r in read(out/'source-index.json')};result={}
    for s in cfg['panels']:
        result[s['code']]=read(out/lookup[s['source']]['path'])
    return result
