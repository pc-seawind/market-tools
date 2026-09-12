"""Read-only live collector. HK supplementation never invents factor/calendar.

Use audited Sina factor series × explicit Tencent raw day prices (NOT qfqday).
Missing/ambiguous raw/anchor stays DATA_BLOCKED with preserved HTTP evidence.
No execution quotes synthesized from daily bars. Capture opens separately.
"""
import ast
import bisect
import json
import urllib.request
from pathlib import Path
from .action_loop import now, read, write
from .timing import digest
from .timing_cli import collect as base_collect, file_hash


def supplement(bundle_path, out):
    b=read(bundle_path); root=Path(out);root.mkdir(parents=True,exist_ok=False)
    for p in b['panels']:
        if p['market']!='HK':continue
        code=p['code'].split('.')[0]; evidence=[]
        try:
            docs={}
            for name,url in [
                ('raw',f'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=hk{code},day,,,200,'),
                ('factors',f'https://finance.sina.com.cn/stock/hkstock/{code}/qfq.js')]:
                path=root/(code+'-'+name+'.raw')
                try:
                    with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0'}),timeout=20) as response:
                        raw=response.read()
                except Exception as e:
                    raw=('FAILED '+type(e).__name__).encode()
                path.write_bytes(raw)
                meta={'name':path.name,'path':str(path.resolve()),'sha256':file_hash(path), 'url':url,
                      'fetched_at':now(),'representation':'exact_HTTP_body_or_explicit_failure'}
                b['inputs'].append(meta);evidence.append(meta['sha256']); docs[name]=raw.decode()
            doc=json.loads(docs['raw'])['data']['hk'+code]
            if 'day' not in doc:
                raise ValueError('HK_raw_day_missing_do_not_treat_qfq_as_raw')
            parsed=ast.literal_eval(docs['factors'].split('=',1)[1].split('\n',1)[0].rstrip(';'))['data']
            ff=sorted((x['d'],float(x['f'])) for x in parsed)
            dates=[d for d,_ in ff]
            rows=[]
            for r in doc['day']:
                if r[0] not in p['sessions']:continue
                i=bisect.bisect_right(dates,r[0])-1
                if i<0:raise ValueError('HK_factor_anchor_missing')
                # Use absolute vendor f, never normalize independently per day/run.
                rows.append({'date':r[0], 'open':float(r[1]),'close':float(r[2]),'high':float(r[3]),
                             'low':float(r[4]),'vol':float(r[5]),'factor':ff[i][1],
                             'close_at':r[0]+'T17:00:00+08:00'})
            if not rows:raise ValueError('HK_no_calendar_aligned_raw_bars')
            p.update(bars=sorted(rows,key=lambda r:r['date']), adjustment='vendor_factor_verified',
                     basis_id='sina_absolute_qfq_factor_times_tencent_raw_v1',fetched_at=now(),
                     factor_evidence=evidence,
                     factor_limitations='vendor_factor_series_not_official_corporate_action_PIT; revisions_block_continuation')
            p.pop('data_gap',None)
            b['errors'].append({'code':p['code'],'remedy_status':'HK_raw_and_absolute_vendor_factor_collected', 'source_sha256':evidence})
        except (KeyError,ValueError,TypeError,SyntaxError,IndexError) as e:
            p['data_gap']=str(e)
            b['errors'].append({'code':p['code'],'remedy_status':'attempted_failed','field':'HK_raw_factor_anchor',
                                'reason':str(e),'source_sha256':evidence})
    b['asof']=now(); b['execution_quotes']=[]
    b['execution_collection']='daily_data_not_execution_evidence; later_contemporaneous_open_snapshot_required'
    write(root/'bundle.json',b)
    return {'bundle':str(root/'bundle.json'),'sha256':file_hash(root/'bundle.json'),'errors':b['errors']}


def collect(scope_path,out):
    root=Path(out);root.mkdir(parents=True,exist_ok=False)
    initial=base_collect(scope_path,root/'initial')
    return supplement(initial['bundle'],root/'supplement')
