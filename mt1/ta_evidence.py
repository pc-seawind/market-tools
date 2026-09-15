"""Worker-executable public research collection. No agent tools/credentials required.
All network attempts persist before admission. Search hits are leads, never facts.
Immutable collection roots provide checkpoints; new roots mean new discoveries.
"""
import argparse
import concurrent.futures
import fcntl
import hashlib
import json
import re
import subprocess
from datetime import timedelta
from pathlib import Path
from urllib.parse import urljoin, urlparse
import xml.etree.ElementTree as ET

from .ta_research import now, read, save, digest, evidence, SCOPE, HERE
from .timing import instant
from .timing_cli import file_hash

CONFIG = HERE / 'docs/ta10/evidence-plan.json'
KINDS = {'company_disclosure', 'industry_statistics', 'media_report',
         'institution_forecast', 'market_narrative', 'internal_hypothesis'}
OWNER = 'investment-agent:existing-daily-and-weekend'


def text_from(raw, encoding=None):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(raw, 'html.parser', from_encoding=encoding)
    for tag in soup(['script', 'style', 'nav', 'footer']): tag.decompose()
    return soup.get_text('\n', strip=True)


def fetch(url, directory, timeout=18):
    """HTTP then Agent-Reach Jina fallback; bounded size/time, persisted failures.
    Auth/paywall/challenge pages are not admitted as source documents.
    """
    import requests
    directory = Path(directory); directory.mkdir(parents=True, exist_ok=True)
    done = directory/'receipt.json'
    if done.exists():
        r = read(done)
        for a in r['attempts']:
            if a.get('raw_path') and file_hash(a['raw_path']) != a['raw_sha256']:
                raise ValueError('cached_source_changed')
        if r.get('text_path') and file_hash(r['text_path']) != r['text_sha256']:
            raise ValueError('cached_text_changed')
        return r
    attempts = []; result = None
    for i, (backend, target) in enumerate([('direct_http', url), ('agent_reach_jina', 'https://r.jina.ai/'+url)]):
        checkpoint = directory/f'attempt-{i}.json'
        if checkpoint.exists():
            a = read(checkpoint); attempts.append(a)
            if a['status']=='ok': result=a; break
            continue
        a = {'backend':backend, 'url':url, 'request_url':target, 'started_at':now()}
        try:
            import time
            start=time.monotonic()
            with requests.get(target, timeout=(6, timeout), stream=True,
                              headers={'User-Agent':'Mozilla/5.0 TA-public-research/1.0'}) as r:
                pieces=[]; size=0
                for chunk in r.iter_content(65536):
                    size+=len(chunk)
                    if size>12_000_000 or time.monotonic()-start>timeout: raise TimeoutError('bounded_download')
                    pieces.append(chunk)
                body=b''.join(pieces); p=directory/f'raw-{i}';p.write_bytes(body)
                a.update(http_status=r.status_code, raw_path=str(p.resolve()),raw_sha256=file_hash(p),fetched_at=now())
                if r.status_code in (401,403,429): a['status']='access_restricted'
                elif r.status_code!=200: a['status']='fetch_failed'
                else:
                    if body.startswith(b'%PDF'):
                        txt=directory/f'text-{i}.txt'
                        cp=subprocess.run(['pdftotext','-layout',str(p),str(txt)],capture_output=True,timeout=25)
                        if cp.returncode: raise ValueError('pdf_extract_failed')
                        text=txt.read_text()
                    else:
                        text=text_from(body, r.encoding if r.encoding and r.encoding.lower()!='iso-8859-1' else None)
                        txt=directory/f'text-{i}.txt';txt.write_text(text)
                    a.update(text_path=str(txt.resolve()),text_sha256=file_hash(txt))
                    # Detect hard challenge, not ordinary login links in a footer.
                    if len(text)<40: a['status']='empty_response'
                    elif re.search(r'百度安全验证|Access Denied|AuthenticationRequiredError|请完成安全验证|人机验证',text[:1200]): a['status']='access_restricted'
                    else:a['status']='ok'
        except Exception as e:a.update(status='fetch_failed',error=type(e).__name__)
        a['ended_at']=now();save(checkpoint,a);attempts.append(a)
        if a['status']=='ok':result=a;break
    out={'url':url,'attempts':attempts,'status':result['status'] if result else ('access_restricted' if any(a['status']=='access_restricted' for a in attempts) else 'fetch_failed'),
         'discovered_at':attempts[0]['started_at']}
    if result:out.update({k:result[k] for k in ('raw_path','raw_sha256','text_path','text_sha256','fetched_at','backend')})
    save(done,out);return out


def task_plan(stock, config, asof):
    profile=config['profiles'].get(stock['code'], {})
    name=stock['name']; days=config.get('news_days',[7,30]); tasks=[]
    def add(topic, query, window, **extra):
        tasks.append({'task_id':digest([stock['code'],topic,query,window])[:20], 'code':stock['code'],
                      'topic':topic,'query':query,'window_days':window,
                      'published_after':(instant(asof)-timedelta(days=window)).isoformat(),
                      'published_before':asof,**extra})
    for window in days:add('news',name,window)
    add('research',name+' 研报 盈利预测 风险',30)
    for relation in ('customers','suppliers','competitors'):
        for entity in profile.get(relation,[]):add(relation,entity+' '+name+' 公告 合作 订单',30,entity=entity)
        if not profile.get(relation):add(relation,name+' '+{'customers':'客户','suppliers':'供应商','competitors':'竞争对手'}[relation]+' 公告',30)
    add('industry',profile.get('industry',name)+' '+profile.get('metric','行业数据')+' 统计',30)
    for p in profile.get('propositions',[]):
        add('proposition',name+' '+p['query'],30,proposition_id=p['id'],test=p['test'])
        add('counterevidence',name+' '+p['counter_query'],30,proposition_id=p['id'],test='寻找反向证据，不以价格证明经营')
    return tasks


def search(task, root, backends=('baidu','bing_rss'), max_hits=3):
    """Search is a real worker HTTP request; no hidden conversation tool fallback."""
    import requests
    from bs4 import BeautifulSoup
    attempts=[]; hits=[]
    for backend in backends:
        query=task['query']+' '+task['published_after'][:10]+' '+task['published_before'][:10]
        url=('https://www.baidu.com/s?wd=' if backend=='baidu' else 'https://www.bing.com/search?format=rss&q=')+requests.utils.quote(query)
        if backend=='eastmoney_news':
            param={'uid':'','keyword':task['query'],'type':['cmsArticle'],'client':'web','clientType':'web','clientVersion':'curr','param':{'cmsArticle':{'searchScope':'default','sort':'time','pageIndex':1,'pageSize':10,'preTag':'','postTag':''}}}
            url='https://search-api-web.eastmoney.com/search/jsonp?cb=callback&param='+requests.utils.quote(json.dumps(param,ensure_ascii=False))

        r=fetch(url,Path(root)/task['task_id']/backend,timeout=12)
        entry={'backend':backend,'query':query,'searched_at':r['discovered_at'],'status':r['status'],'receipt':str((Path(root)/task['task_id']/backend/'receipt.json').resolve()),'hits':0}
        if r['status']=='ok':
            raw=Path(r['raw_path']).read_bytes()
            try:
                if backend=='eastmoney_news':
                    decoded=json.loads(raw.decode().strip().removeprefix('callback(').removesuffix(');').removesuffix(')'))
                    found=[{'url':'https://finance.eastmoney.com/a/'+x['code']+'.html','title':x['title'],'search_published_at':x['date']} for x in decoded.get('result',{}).get('cmsArticle',[]) if task['published_after'][:10]<=x['date'][:10]<=task['published_before'][:10]]
                elif backend=='bing_rss':
                    tree=ET.fromstring(raw)
                    found=[{'url':x.findtext('link'),'title':x.findtext('title'),'search_published_at':x.findtext('pubDate')} for x in tree.findall('.//item')]
                else:
                    soup=BeautifulSoup(raw,'html.parser'); found=[{'url':a.get('href'),'title':a.get_text()} for a in soup.select('h3 a')]
                hits.extend(x for x in found if x.get('url','').startswith('http'))
                entry['hits']=len(found);entry['status']='hits' if found else 'searched_no_results'
            except (ET.ParseError,ValueError):entry['status']='parse_failed'
        attempts.append(entry)
        if hits:break
    hits=list({h['url']:h for h in hits}.values())[:max_hits]
    return {**task,'attempts':attempts,'hits':hits,'status':'hits' if hits else ('access_restricted' if any(a['status']=='access_restricted' for a in attempts) else 'searched_no_results' if any(a['status']=='searched_no_results' for a in attempts) else 'search_failed')}


def announcement_discovery(stock, root, asof, entity=None):
    """Public exchange-announcement index + full original PDF, not snippets.
    A related issuer is a research lead, not proof of a supply relationship.
    """
    code=(entity or stock)['code'];name=(entity or stock)['name']
    url='https://np-anotice-stock.eastmoney.com/api/security/ann?sr=-1&page_size=40&page_index=1&ann_type=A&stock_list='+code.split('.')[0]
    base=Path(root)/('announcements-'+code);receipt=fetch(url,base/'index');specs=[];failures=[]
    if receipt['status']=='ok':
        try:
            rows=read(receipt['raw_path'])['data']['list']
            eligible=[r for r in rows if (instant(asof)-timedelta(days=30)).date().isoformat()<=r['notice_date'][:10]<=asof[:10]]
            # Prefer business content; explicitly don't call a buyback supplier evidence.
            eligible.sort(key=lambda r:bool(re.search('合作|增资|经营|业绩|投资者关系|调研|合同|销售|产能',r['title'])),reverse=True)
            for item in eligible[:2]:
                detail='https://np-cnotice-stock.eastmoney.com/api/content/ann?art_code='+item['art_code']+'&client_source=web&page_index=1'
                dr=fetch(detail,base/item['art_code'])
                if dr['status']!='ok':failures.append(dr);continue
                d=read(dr['raw_path'])['data'];pdf=d.get('attach_url')
                if not pdf:continue
                date=item['notice_date'][:10]
                specs.append({'url':pdf,'title':item['title'],'kind':'company_disclosure','institution':name,
                              'published_at':date+'T23:59:59+08:00','verify':[re.escape(code.split('.')[0])],
                              'topics':['company_current'],'terms':['客户','供应商','竞争','原材料','毛利','风险','订单','产能'],
                              'origin_key':item['art_code'],'original_url':'https://data.eastmoney.com/notices/detail/'+code.split('.')[0]+'/'+item['art_code']+'.html',
                              'discovery_index':url,'related_entity_role':(entity or {}).get('role','self'),
                              'relationship_status':'not_certified_by_issuer_identity'})
        except (KeyError,TypeError,ValueError):failures.append({'status':'parse_failed'})
    return specs,{'task_id':'ann-index-'+code,'topic':'announcement_search','code':stock['code'],'query':name+' 公告 最近三十天',
                  'status':'hits' if specs else receipt['status'] if receipt['status']!='ok' else 'searched_no_results',
                  'attempts':[receipt]+failures,'hits':specs,'window_days':30}


def report_discovery(stock, root, asof):
    """Sina public broker index: verified dates/authors from each actual row."""
    from bs4 import BeautifulSoup
    code=stock['code'];symbol=code.split('.')[1].lower()+code.split('.')[0]
    url='https://stock.finance.sina.com.cn/stock/go.php/vReport_List/kind/search/index.phtml?symbol='+symbol+'&t1=2'
    receipt=fetch(url,Path(root)/'broker-index')
    specs=[]
    if receipt['status']=='ok':
        raw=Path(receipt['raw_path']).read_bytes()
        # This public endpoint is GBK; avoid heuristic mojibake.
        soup=BeautifulSoup(raw.decode('gb18030',errors='replace'),'html.parser')
        for tr in soup.find_all('tr'):
            a=tr.find('a',href=re.compile('vReport_Show'))
            if not a:continue
            cells=[x.get_text(' ',strip=True) for x in tr.find_all('td')]
            date=next((x for x in cells if re.fullmatch(r'20\d{2}-\d{2}-\d{2}',x)),None)
            if not date or not (instant(asof)-timedelta(days=30)).date().isoformat()<=date<=asof[:10]:continue
            date_index=cells.index(date);title=a.get_text(' ',strip=True)
            specs.append({'url':urljoin(url,a['href']),'title':title,'kind':'institution_forecast',
                          'institution':cells[date_index+1] if len(cells)>date_index+1 else 'unknown',
                          'author':cells[date_index+2] if len(cells)>date_index+2 else 'unknown',
                          'published_at':date+'T23:59:59+08:00','date_token':date,
                          'verify':[re.escape(stock['name'])],'topics':['research'],
                          'terms':['风险','预测','成本','客户','竞争'],'discovery_index':url})
            if len(specs)>=2:break
    return specs,{'task_id':'broker-index-'+code,'code':code,'topic':'research','query':symbol,
                  'status':'hits' if specs else receipt['status'] if receipt['status']!='ok' else 'searched_no_results',
                  'attempts':[receipt],'hits':specs,'window_days':30}


def sections(body, terms, budget=14000):
    """Verbatim risk/topic windows (not financial-keyword-only); exact offsets."""
    if len(body)<=budget:return [{'text':body,'char_start':0,'char_end':len(body),'page':1,'paragraph':1}]
    ranges=[]
    for term in terms:
        for m in list(re.finditer(re.escape(term),body,re.I))[:3]:ranges.append((max(0,m.start()-250),min(len(body),m.end()+1000)))
    ranges.append((0,min(len(body),1500)))
    merged=[]
    for start,end in sorted(ranges):
        if merged and start<=merged[-1][1]:merged[-1]=(merged[-1][0],max(end,merged[-1][1]))
        else:merged.append((start,end))
    out=[];remaining=budget
    for start,end in merged:
        end=min(end,start+remaining)
        if end<=start:break
        out.append({'text':body[start:end],'char_start':start,'char_end':end,'page':body[:start].count('\f')+1,'paragraph':body[:start].count('\n')+1});remaining-=end-start
    return out


def numeric_extract(body, specs, kind, published_at):
    """Only reviewed semantic selectors can enter the model numeric whitelist.
    Discovery regex candidates are NOT metrics; no auto-inferred unit/subject.
    """
    out={}
    for spec in specs:
        matches=list(re.finditer(spec['pattern'],body,re.S))
        if len(matches)!=1:continue
        m=matches[0]; raw=m.group('value');value=raw.replace(',','')
        n={k:v for k,v in spec.items() if k!='pattern'}
        from decimal import Decimal
        factor=str(spec.get('conversion_factor','1'))
        value=str(Decimal(value)*Decimal(factor))
        n.update(raw_value=raw,value=value,char_start=m.start('value'),char_end=m.end('value'),
                 page=body[:m.start('value')].count('\f')+1,paragraph=body[:m.start('value')].count('\n')+1,
                 context=body[m.start():m.end()],evidence_type=kind,formula='raw_value * '+factor,conversion_factor=factor,publication=published_at)
        n['validation_errors']=metric_errors(n,body,kind,published_at)
        if not n['validation_errors']:out[spec['field']]=n
    return out


def metric_errors(n, body, kind, cutoff):
    errors=[]
    for k in ('subject','metric','unit','currency','period_start','period_end','scope','basis','is_forecast'):
        if n.get(k) in (None,''):errors.append('missing_'+k)
    try:
        from decimal import Decimal
        if not Decimal(str(n['value'])).is_finite():errors.append('nonfinite')
        if body[n['char_start']:n['char_end']]!=n['raw_value']:errors.append('locator_mismatch')
        if Decimal(n['raw_value'].replace(',',''))*Decimal(n['conversion_factor'])!=Decimal(str(n['value'])):errors.append('conversion_mismatch')
        if n['period_end']<n['period_start']:errors.append('period_order')
        if not n['is_forecast'] and n['period_end']>cutoff[:10]:errors.append('future_actual_period')
        if n['is_forecast'] and kind not in ('institution_forecast','company_disclosure'):errors.append('forecast_kind_mismatch')
    except (KeyError,ValueError,TypeError,ArithmeticError):errors.append('invalid_metric')
    return errors


def admit(code, spec, receipt, asof):
    if receipt['status']!='ok':return None,receipt['status']
    body=Path(receipt['text_path']).read_text(); pub=spec.get('published_at');kind=spec.get('kind','media_report')
    if not pub:return None,'publication_unknown'
    if instant(pub)>instant(asof):return None,'future_publication'
    if kind not in KINDS:return None,'unclassified_source'
    if not all(re.search(p,body,re.I) for p in spec.get('verify',[])):return None,'identity_or_publication_not_verified'
    # Explicit curated metadata requires a verifiable publication date token.
    if spec.get('date_token') and spec['date_token'] not in body:return None,'publication_not_in_original'
    nums=numeric_extract(body,spec.get('numbers',[]),kind,pub)
    content={'sections':sections(body,spec.get('terms',['客户','成本','风险','预测'])),
             'excerpt_scope':'topic_driven_verbatim_full_source_archived','full_text_chars':len(body),
             'numeric_metrics':nums,'source_title':spec.get('title'),'interpretation_rule':'预测及媒体因果解释不是公司事实；未披露采购份额保持未知'}
    e=evidence(code,kind,content,spec['url'],pub,receipt['fetched_at'],receipt['raw_path'],
               text_path=receipt['text_path'],text_sha256=receipt['text_sha256'],discovered_at=receipt['discovered_at'],
               original_institution=spec.get('institution','unknown'),author=spec.get('author','unknown'),
               original_url=spec.get('original_url',spec['url']),reprint_chain=spec.get('reprint_chain',[]),
               independence_key=spec.get('origin_key',spec.get('original_url',spec['url'])),
               provenance_status='public_body_archived_not_PIT_certified',publication_precision=spec.get('publication_precision','date_only'),
               topics=spec.get('topics',[]),stance=spec.get('stance','unclassified'),
               background=spec.get('background',False),related_entity_role=spec.get('related_entity_role'),relationship_status=spec.get('relationship_status'),
               semantic_review='source_metadata_checked_not_independent_acceptance')
    return e,None


def official_index_discovery(stock, root, config, asof):
    """Public company IR fallbacks; only source-reviewed metadata templates.
    URLs of current documents are discovered from the archived official index.
    """
    from bs4 import BeautifulSoup
    specs=[];receipts=[]
    for plan in config['profiles'].get(stock['code'],{}).get('discovery_indexes',[])[:3]:
        r=fetch(plan['url'],Path(root)/'official-index'/digest(plan['url'])[:20])
        hits=[]
        if r['status']=='ok':
            soup=BeautifulSoup(Path(r['raw_path']).read_bytes(),'html.parser')
            for a in soup.find_all('a',href=True):
                url=urljoin(plan['url'],a['href']);title=a.get_text(' ',strip=True)
                if re.search(plan['link_pattern'],url) and re.search(plan.get('title_pattern','.*'),title):
                    hits.append({'url':url,'title':title})
            hits=list({h['url']:h for h in hits}.values())[:2]
            specs.extend({**plan['spec'],'url':h['url']} for h in hits)
        receipts.append({'task_id':'official-index-'+digest(plan['url'])[:16],'topic':'company_current',
                         'query':plan['url'],'backend':'official_ir_http','searched_at':r['discovered_at'],
                         'status':'hits' if hits else (r['status'] if r['status']!='ok' else 'searched_no_results'),
                         'hits':hits,'receipt':r})
    return specs,receipts


def review_lead(stock, lead, receipt, config, asof):
    """Review full archived body, NEVER title/snippet, before normal admission.
    Policies certify only publisher/type, not the article's claims or causality.
    Unsupported publishers or ambiguous metadata remain pending with reasons.
    """
    if receipt.get('status') != 'ok':
        return None, {'status':'pending','reason':receipt.get('status','not_fetched')}
    body = Path(receipt['text_path']).read_text()
    host = urlparse(lead['url']).hostname
    policy = config.get('lead_publishers', {}).get(host)
    if not policy:
        return None, {'status':'pending','reason':'publisher_policy_missing'}
    # Hash-bound manual review can handle a source whose HTML has no metadata.
    reviewed = config.get('lead_reviews', {}).get(lead['url'])
    if reviewed:
        if reviewed.get('text_sha256') != receipt['text_sha256'] or not reviewed.get('reviewer'):
            return None, {'status':'pending','reason':'review_identity_mismatch'}
        spec = {**reviewed['spec'], 'url':lead['url']}
    else:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(Path(receipt['raw_path']).read_bytes(), 'html.parser')
        def meta(*keys):
            values = {x.get('content','').strip() for x in soup.find_all('meta')
                      if x.get('property', x.get('name','')) in keys and x.get('content')}
            return next(iter(values)) if len(values)==1 else None
        pub = meta('article:published_time','datePublished','publishdate','pubdate')
        author = meta('author','article:author')
        institution=policy['institution']
        if host=='finance.eastmoney.com':
            dates=set(re.findall(r'(20\d{2})年(\d{2})月(\d{2})日 (\d{2}:\d{2})',body))
            authors=re.findall(r'作者：\s*([^\n]+)',body)
            sources=re.findall(r'文章来源：([^\n）]+)',body)
            if len(dates)==1 and len(set(authors))==1 and len(set(sources))==1:
                y,m,d,hm=next(iter(dates));pub=f'{y}-{m}-{d}T{hm}:00+08:00'
                author=authors[0].strip();institution=sources[0].strip()
        title = meta('og:title') or (soup.title.get_text().strip() if soup.title else None)
        if not pub or not author or not title or stock['name'] not in body:
            return None, {'status':'pending','reason':'publication_author_or_subject_ambiguous'}
        # Generic classification is deliberately limited to media reporting.
        # Primary/forecast/stats need a human-reviewed source-specific spec.
        if policy.get('kind') != 'media_report':
            return None, {'status':'pending','reason':'specialist_classification_requires_review'}
        try:
            if instant(pub)>instant(asof):raise ValueError('future')
        except (ValueError,TypeError):
            return None, {'status':'pending','reason':'publication_invalid_or_future'}
        spec = {'url':lead['url'],'title':title,'published_at':pub,'author':author,
                'institution':institution,'kind':'media_report','topics':['news'],
                'verify':[re.escape(stock['name'])], 'terms':[stock['name']],
                'reprint_chain':[institution,policy['institution']],
                'origin_key':institution+':'+title}
    e, error = admit(stock['code'],spec,receipt,asof)
    if error:return None, {'status':'pending','reason':error}
    e['discovery_task_id']=lead['task_id']
    e['admission_review']={'method':'hash_bound_source_review' if reviewed else 'publisher_and_html_metadata_only',
                           'not_fact_certification':True,'reviewed_at':now()}
    return e, {'status':'admitted','evidence_id':e['evidence_id'],'review':e['admission_review']}


def proposition_verification(profile, es, asof):
    """Binding is to the actual body hash + quoted span + stock evidence IDs.
    Unknown is a researched observation limit, not confirmed impairment.
    This is developer/source review, never independent publication sign-off.
    """
    by={e['evidence_id']:e for e in es};out=[]
    for p in profile.get('propositions',[]):
        review=profile.get('proposition_reviews',{}).get(p['id'],{})
        errors=[];bindings=review.get('bindings',[])
        for b in bindings:
            e=by.get(b.get('evidence_id'))
            if not e and b.get('url'):
                matches=[x for x in es if x.get('url')==b['url'] and x.get('text_sha256')==b.get('text_sha256')]
                e=matches[0] if len(matches)==1 else None
            if not e or e.get('kind') not in ('company_disclosure','industry_statistics','company_primary'):
                errors.append('wrong_or_missing_evidence');continue
            try:
                body=Path(e['text_path']).read_text()
                if file_hash(e['text_path'])!=b.get('text_sha256') or not b.get('quote') or b['quote'] not in body:
                    errors.append('binding_mismatch')
            except (KeyError,OSError):errors.append('body_missing')
        state=review.get('status','missing')
        if state not in ('verified','unknown','missing'):errors.append('invalid_verification_status')
        if state!='missing' and (not bindings or not review.get('reviewer') or not review.get('rationale')):
            errors.append('incomplete_review')
        if state=='unknown' and not review.get('observation_limit'):errors.append('unknown_without_limit')
        if errors:state='missing'
        out.append({'proposition':p['id'],'test':p['test'],'status':state,
                    'errors':errors,'bindings':bindings,'review':review,
                    'reason':'quantitative_link_not_publicly_verified' if state=='missing' else state,
                    'owner':OWNER,'next_check_at':(instant(asof)+timedelta(days=1)).isoformat(),
                    'remedy':p.get('remedy',p['test'])})
    return out


def coverage(stock, searches, es, config, asof):
    """Substantive coverage independent of model/protocol pass, fail closed.
    A retrieved list is not a researched risk. Each critical proposition needs
    both primary/independent material and counterevidence, not N mirrors.
    """
    profile=config['profiles'].get(stock['code'],{}); rows=[]
    for topic in ('news','research','customers','suppliers','competitors','industry','proposition','counterevidence'):
        attempted=[s for s in searches if s['topic']==topic]
        relevant=[e for e in es if topic in e.get('topics',[])]
        qualified=[e for e in relevant if (topic not in ('customers','suppliers','competitors','industry','proposition') or e['kind'] in ('company_disclosure','industry_statistics'))]
        # Coverage certifies neither numerical completeness nor causality.
        reason='retrieved_substantive_body' if qualified and attempted else ('not_searched' if not attempted else 'no_verified_substantive_body')
        if not qualified and attempted and all(s['status']=='access_restricted' for s in attempted):reason='access_restricted'
        rows.append({'topic':topic,'status':'pass' if qualified and attempted else 'blocked','reason':reason,
                     'evidence_ids':[e['evidence_id'] for e in qualified], 'searched_tasks':[s['task_id'] for s in attempted],
                     'owner':OWNER,'next_check_at':(instant(asof)+timedelta(days=1)).isoformat(),
                     'remedy':profile.get('remedies',{}).get(topic,'取得'+stock['name']+'的'+topic+'原文并核主体、统计期间、反向证据；不能用搜索列表代替')})
    verification=proposition_verification(profile,es,asof)
    unresolved=[v for v in verification if v['status']=='missing']
    retrieval='blocked' if any(r['status']=='blocked' for r in rows) else 'pass'
    return {'code':stock['code'],'name':stock['name'],'status':'blocked' if unresolved or retrieval=='blocked' else 'pass',
            'retrieval_status':retrieval,'proposition_verification':verification,
            'quantification_status':'unknown' if any(v['status']=='unknown' for v in verification) else ('missing' if unresolved else 'verified'),
            'publication_status':'independent_review_required',
            'rows':rows,'unresolved':unresolved,'scope':'research_coverage_not_protocol_or_independent_review'}



def collect(root, scope_path=SCOPE, config_path=CONFIG, asof=None):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    with (root/'.collection.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        done=root/'collection.json'; identity={'scope_hash':file_hash(scope_path),'config_hash':file_hash(config_path)}
        if done.exists():
            prior=read(done)
            if prior['identity']!=identity:raise ValueError('new_collection_revision_required')
            for s in prior['stocks']:
                for e in s['evidence']:
                    if file_hash(e['raw_path'])!=e['raw_sha256'] or file_hash(e['text_path'])!=e['text_sha256']:raise ValueError('collection_source_changed')
            return prior
        config=read(config_path);scope=read(scope_path)
        save(root/'plan.json',config)
        intent=root/'intent.json'
        if not intent.exists():save(intent,{'identity':identity,'started_at':asof or now()})
        if read(intent)['identity']!=identity:raise ValueError('collection_config_changed_new_root_required')
        started=read(intent)['started_at'];stocks=[]
        for stock in scope['confirmed_holdings']:
            sd=root/stock['code'];sd.mkdir(exist_ok=True)
            if (sd/'stock.json').exists():stocks.append(read(sd/'stock.json'));continue
            tasks=task_plan(stock,config,started)
            def one(t):
                p=sd/'tasks'/(t['task_id']+'.json')
                if p.exists():return read(p)
                try:r=search(t,sd/'search',config.get('search_backends',['baidu','bing_rss']),config.get('max_hits',2))
                except Exception as e:r={**t,'status':'search_failed','hits':[],'attempts':[{'error':type(e).__name__,'at':now()}]}
                save(p,r);return r
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(4,config.get('concurrency',3))) as pool:
                searches=list(pool.map(one,tasks))
            specs=list(config['profiles'].get(stock['code'],{}).get('sources',[]))
            try:discovered,index_receipt=report_discovery(stock,sd,started)
            except Exception as e:discovered,index_receipt=[],{'task_id':'broker-index-'+stock['code'],'topic':'research','status':'search_failed','attempts':[{'error':type(e).__name__}],'hits':[]}
            searches.append(index_receipt);specs.extend(discovered)
            try:ann,ann_receipt=announcement_discovery(stock,sd,started)
            except Exception as e:ann,ann_receipt=[],{'task_id':'ann-index-'+stock['code'],'topic':'announcement_search','status':'search_failed','attempts':[{'error':type(e).__name__}],'hits':[]}
            searches.append(ann_receipt);specs.extend(ann)
            for entity in config['profiles'].get(stock['code'],{}).get('related_issuers',[])[:3]:
                try:ann,ann_receipt=announcement_discovery(stock,sd,started,entity)
                except Exception as e:ann,ann_receipt=[],{'task_id':'ann-index-'+entity['code'],'topic':'announcement_search','status':'search_failed','attempts':[{'error':type(e).__name__}],'hits':[]}
                searches.append(ann_receipt);specs.extend(ann)
            try:
                official,ir_receipts=official_index_discovery(stock,sd,config,started)
                specs.extend(official);searches.extend(ir_receipts)
            except Exception as exc:
                searches.append({'task_id':'official-ir-'+stock['code'],'topic':'company_current','status':'search_failed','hits':[],'error':type(exc).__name__})
            # Search leads get full bodies too; without verified provenance they
            # remain leads, not magically promoted to company facts.
            leads=[]
            for s in searches:
                for h in s['hits']:
                    if stock['name'] in h.get('title','') or s.get('entity','\0') in h.get('title',''):
                        leads.append({**h,'topic':s['topic'],'task_id':s['task_id']})
            leads=list({h['url']:h for h in leads}.values())[:config.get('max_lead_bodies',4)]
            urls=list(dict.fromkeys([s['url'] for s in specs]+[h['url'] for h in leads])); receipts={}
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
                for url,r in zip(urls,pool.map(lambda u:fetch(u,sd/'documents'/digest(u)[:20]),urls)):receipts[url]=r
            es=[];rejected=[];seen=set()
            for spec in specs:
                try:e,err=admit(stock['code'],spec,receipts[spec['url']],now())
                except Exception as error:e,err=None,'admission_failed:'+type(error).__name__
                if e:
                    dedup=e['independence_key']
                    # Reprints of the same original are one independent source.
                    if dedup in seen:continue
                    seen.add(dedup);es.append(e)
                else:rejected.append({'url':spec['url'],'reason':err,'receipt':receipts[spec['url']]})
            lead_reviews=[]
            for lead in leads:
                try:e,review=review_lead(stock,lead,receipts[lead['url']],config,now())
                except Exception as exc:e,review=None,{'status':'pending','reason':'review_failed:'+type(exc).__name__}
                lead_reviews.append({'url':lead['url'],'task_id':lead['task_id'],**review})
                if e and e['independence_key'] not in seen:
                    seen.add(e['independence_key']);es.append(e)
            row={**stock,'tasks':searches,'leads':leads,'lead_reviews':lead_reviews,'evidence':es,'rejected':rejected,
                 'coverage':coverage(stock,searches,es,config,now())}
            save(sd/'stock.json',row);stocks.append(row)
        out={'version':'TA-evidence-1','identity':identity,'started_at':started,'as_of':now(),'research_mode':'current_research',
             'historical_reconstruction':'not_PIT_certified; actual_discovered_at_retained; never_rewrite_old_run', 'stocks':stocks}
        save(done,out);return out


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--scope',default=str(SCOPE));p.add_argument('--config',default=str(CONFIG));a=p.parse_args()
    r=collect(a.root,a.scope,a.config)
    print(json.dumps({'collection':str(Path(a.root)/'collection.json'),'stocks':[{'code':s['code'],'coverage':s['coverage']['status'],'evidence_count':len(s['evidence'])} for s in r['stocks']]},ensure_ascii=False))


def verify_collection(data):
    for stock in data['stocks']:
        for e in stock['evidence']:
            if file_hash(e['raw_path'])!=e['raw_sha256'] or file_hash(e['text_path'])!=e['text_sha256']:
                raise ValueError('collection_source_changed')
    return data


def reclassify(parent, root, config_path=CONFIG):
    """New metadata/numeric revision from immutable fetched originals, NOT a fetch.
    Only selectors/source metadata can change. New queries require collect().
    """
    old=verify_collection(read(parent));cfg=read(config_path);root=Path(root);rows=[]
    for stock in old['stocks']:
        old_tasks=task_plan(stock,read(Path(parent).parent/'plan.json'),old['started_at']) if (Path(parent).parent/'plan.json').exists() else None
        if {t['task_id'] for t in stock['tasks'] if not t['task_id'].startswith(('broker-index-','ann-index-'))}!={t['task_id'] for t in task_plan(stock,cfg,old['started_at'])}:raise ValueError('new_search_tasks_require_collection')
        byurl={e['url']:e for e in stock['evidence']};es=[];rejected=list(stock['rejected'])
        for prior in stock['evidence']:
            spec=next((s for s in cfg['profiles'].get(stock['code'],{}).get('sources',[]) if s['url']==prior['url']),None)
            if not spec:es.append(prior);continue
            receipt={**prior,'status':'ok'};e,err=admit(stock['code'],spec,receipt,old['as_of'])
            if e:es.append(e)
            else:rejected.append({'url':spec['url'],'reason':err})
        from .ta_numeric import conflicts
        rows.append({**stock,'evidence':es,'numeric_conflicts':conflicts(es),'rejected':rejected,'coverage':coverage(stock,stock['tasks'],es,cfg,old['as_of'])})
    out={**old,'identity':{**old['identity'],'config_hash':file_hash(config_path)},'stocks':rows,
         'parent_collection_hash':file_hash(parent),'revision_at':now(),'reuse':'same_raw_originals_no_new_fetch_discovered_at_unchanged'}
    save(root/'collection.json',out);return out


def supplement(parent, root, config_path=CONFIG):
    """Bounded new-source revision; existing retrieved bodies are explicitly reused.
    Existing queries must match. A new query requires a fresh collect root.
    """
    old=verify_collection(read(parent));cfg=read(config_path);root=Path(root);stocks=[]
    done=root/'collection.json'
    if done.exists():
        r=verify_collection(read(done))
        if r['parent_collection_hash']!=file_hash(parent) or r['identity']['config_hash']!=file_hash(config_path):raise ValueError('new_revision_required')
        return r
    for stock in old['stocks']:
        if {t['task_id'] for t in stock['tasks'] if not t['task_id'].startswith(('broker-index-','ann-index-'))}!={t['task_id'] for t in task_plan(stock,cfg,old['started_at'])}:raise ValueError('new_search_tasks_require_collection')
        es=[];rejected=[];by={e['url']:e for e in stock['evidence']};specs=cfg['profiles'].get(stock['code'],{}).get('sources',[])
        for spec in specs:
            prior=by.pop(spec['url'],None)
            receipt={**prior,'status':'ok'} if prior else fetch(spec['url'],root/stock['code']/'documents'/digest(spec['url'])[:20])
            e,err=admit(stock['code'],spec,receipt,now())
            if e:es.append(e)
            else:rejected.append({'url':spec['url'],'reason':err,'receipt':receipt})
        es+=list(by.values());seen_origin=set();seen_raw=set();unique=[];aliases=[]
        for e in es:
            if e['independence_key'] in seen_origin or e['raw_sha256'] in seen_raw:
                aliases.append({'url':e['url'],'origin':e['independence_key'],'raw_sha256':e['raw_sha256'],'reason':'duplicate_original_not_independent'});continue
            seen_origin.add(e['independence_key']);seen_raw.add(e['raw_sha256']);unique.append(e)
        from .ta_numeric import conflicts
        stocks.append({**stock,'evidence':unique,'rejected':rejected,'aliases':aliases,'numeric_conflicts':conflicts(unique),'coverage':coverage(stock,stock['tasks'],unique,cfg,now())})
    result={**old,'identity':{**old['identity'],'config_hash':file_hash(config_path)},'stocks':stocks,'as_of':now(),
            'parent_collection_hash':file_hash(parent),'reuse':'prior originals retained; missing configured sources actually fetched; see each discovered_at'}
    save(done,result);return result
