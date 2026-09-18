"""Read-only natural report audit; only writes the selected new audit output."""
import hashlib,json,re,sys
from pathlib import Path

def read(p):return json.loads(Path(p).read_text())
def digest(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def norm(s):return re.sub(r'[^\w]','',re.sub(r'\[([^\]]+)\]\([^)]*\)',r'\1',s))

def audit(report,phase,documents,out):
    report=Path(report);base=report/('huatai-'+phase)
    m=read(base/'manifest.json');complete=read(base/'complete.json')
    consumers=sorted(report.glob('*/huatai-consumer-receipt.json'))
    # Choose the actual published hash, not just the first technical rendering.
    final=report/'report.md'
    if not final.exists(): final=report/'consumer-reviewed/daily.md'
    body=final.read_text();h=digest(final)
    receipts=[read(p) for p in consumers]
    assert any(r['report_sha256']==h for r in receipts)
    directory=next(p.parent for p,r in zip(consumers,receipts) if r['report_sha256']==h)
    parts=read(directory/'publication-parts.json')
    assert ''.join(Path(p['path']).read_text() for p in parts['parts'])==body
    cloud=''.join(read(p)['data']['content'].split('\n',1)[-1] for p in documents)
    assert norm(body) in norm(cloud), 'published_report_not_fully_present'
    rows=[]
    for s in m['stocks']:
        d=base/s['code'];r=read(d/'result.json'); raw=list(d.glob('response-*.json'))
        assert len(raw)==len(r['attempts'])
        for a in r['attempts']:assert digest(a['path'])==a['sha256']
        if r['ok']:
            assert read(r['attempts'][-1]['path'])['data']['answer']==r['answer']
            assert hashlib.sha256(r['answer'].encode()).hexdigest()==r['answer_sha256']
            assert r['answer'] in body
            assert norm(r['answer']) in norm(cloud),s['code']
        else:
            assert s['code'] in body and '咨询失败' in body
            assert norm(r['error']['message']) in norm(cloud)
        rows.append({'code':s['code'],'ok':r['ok'],'requests':len(raw),'answer_sha256':r['answer_sha256'],'full_local_and_cloud_verified':True})
    for r in receipts:assert r['run_id']==m['run_id']
    original={name:((report/(name+'.md')).read_text() in body) for name in ('first','second','company')}
    assert all(original.values())
    message=read(report/'message-send.json');assert message.get('ok') is True
    result={'phase':phase,'report_directory':str(report),'run_id':m['run_id'],'asof':m['asof'],
            'finished_at':complete['completed_at'],'scope_sha256':m['scope_sha256'],'rows':rows,
            'report_sha256':h,'final_consumer':str(directory),'consumer_passes':len(consumers),
            'one_run_across_review_passes':True,'original_sections_preserved':original,
            'documents':[{ 'readback':str(p),'sha256':digest(p)} for p in documents],
            'published_message':message['data'],'publication_parts':parts,
            'failed_count':sum(not r['ok'] for r in rows),
            'failure_visibility':'本自然批次如无失败则无自然失败样本；故障隔离回归仍有效，不伪造失败'}
    Path(out).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:result[k] for k in ('phase','run_id','asof','report_sha256','published_message','consumer_passes')},ensure_ascii=False))

if __name__=='__main__':audit(sys.argv[1],sys.argv[2],sys.argv[4:],sys.argv[3])
