"""Bounded, verbatim section extraction; retains full source and exact offsets.
No LLM-generated snippets. Source catalog is versioned independently of holdings.
"""
import re
from pathlib import Path
from .ta_research import read, evidence
from .timing_cli import file_hash

TOPICS={
 'performance':r'主要会计数据|主要會計|经营情况的讨论|業績摘要|FINANCIAL HIGHLIGHTS|Financial Highlights|Financial Summary|BUSINESS REVIEW',
 'operations':r'主营业务分析|主營業務|经营情况讨论|经营情况的讨论|Management Discussion|MANAGEMENT DISCUSSION|Business Review|分产品|分部业绩|分部業績',
 'cash_flow':r'经营活动产生的现金流量净额|經營活動產生|Net cash (?:generated|used)|net cash (?:generated|used)|Free cash flow|free cash flow|CONSOLIDATED.*CASH',
 'inventory':r'存货|存貨|Inventories|inventories',
 'segments':r'分行业|分产品|分部信息|分部资料|分部資料|Segment information|segment information|Segment results|Core local commerce|Smartphone',
 'capex':r'资本开支|資本開支|购建固定资产|Capital expenditures|capital expenditures|Payments for.*property',
}


def excerpts(body, max_chars=10000):
    chunks=[];ranges=[]
    for topic,pattern in TOPICS.items():
        pattern=re.sub(r'([\u4e00-\u9fff])',lambda m:m.group(1)+r'\s*',pattern)
        matches=list(re.finditer(pattern,body,re.I))
        if not matches:continue
        # Skip table-of-contents hits: require some substantive content around it.
        eligible=[m for m in matches if not re.search(r'\.{4,}',body[m.start():m.start()+100])]
        m=(eligible or matches)[0];start=max(0,m.start()-180);end=min(len(body),m.end()+1300)
        ranges.append((start,end));chunks.append({'topic':topic,'page':body[:start].count('\f')+1 if '\f' in body else None,
              'char_start':start,'char_end':end,'text':body[start:end]})
    # Small press releases can be sent whole, up to the same bounded budget.
    if not chunks:chunks=[{'topic':'source_start','page':1,'char_start':0,'char_end':min(len(body),max_chars),'text':body[:max_chars]}]
    return chunks


def load_primary(code, directory, raw):
    if not directory:raise ValueError('source_catalog_missing')
    meta=read(Path(directory)/(code+'.meta.json'));src=Path(meta['file']);txt=Path(meta['text']);body=txt.read_text()
    if code.split('.')[0].lstrip('0') not in body or '2026' not in body:raise ValueError('company_identity_not_in_source')
    if meta.get('extract_status')!=0:raise ValueError('source_extract_failed')
    if meta.get('sha256') and file_hash(src)!=meta['sha256']:raise ValueError('catalog_source_changed')
    dest=Path(raw)/(code+'.company'+src.suffix);dest.write_bytes(src.read_bytes());dt=Path(raw)/(code+'.company.txt');dt.write_bytes(txt.read_bytes())
    chunks=excerpts(body)
    content={'sections':chunks,'excerpt_scope':'bounded_topic_windows_full_original_archived','full_text_chars':len(body),'selected_chars':sum(len(c['text']) for c in chunks),'missing_topics':[k for k in TOPICS if k not in {c['topic'] for c in chunks}], 'mirror':meta.get('mirror')}
    result=[evidence(code,'company_primary',content,meta['url'],meta['published_at'],meta['fetched_at'],dest,currency='CNY',unit='see_each_original_table_unit_NOT_quote_currency',adjustment='not_applicable',publication_precision=meta['publication_precision'],text_path=str(dt.resolve()),text_sha256=file_hash(dt))]
    for i,event in enumerate(meta.get('events',[])):
        ep=Path(event['file']);et=Path(event['text']);ed=Path(raw)/(code+f'.event{i}'+ep.suffix);ed.write_bytes(ep.read_bytes());td=Path(raw)/(code+f'.event{i}.txt');td.write_bytes(et.read_bytes())
        result.append(evidence(code,'company_primary',{'sections':event.get('sections') or [{'text':et.read_text()[:6500],'char_start':0,'char_end':min(len(et.read_text()),6500)}],'event_date':event.get('event_date'),'excerpt_scope':'bounded_event_original_full_archived'},event['url'],event['published_at'],event['fetched_at'],ed,publication_precision=event['publication_precision'],text_path=str(td.resolve()),text_sha256=file_hash(td)))
    return result
