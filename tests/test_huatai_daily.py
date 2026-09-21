import json
from pathlib import Path
import pytest
from mt1 import huatai_daily as h


def scope(tmp_path):
    p = tmp_path / 'scope.json'
    p.write_text(json.dumps({'confirmed_holdings': [{'code': '001309.SZ', 'name': '德明利', 'market': 'CN'}, {'code': '00700.HK', 'name': '腾讯', 'market': 'HK'}]}))
    return p


def test_complete_verbatim_idempotent_and_new_report(tmp_path):
    s = scope(tmp_path); base = tmp_path / 'run1'
    h.prepare(base, 'morning', s, '2026-09-18T07:15:00+08:00')
    answer = '# 标题\n\n|a|b|\n|---|---|\n|甲|乙|\n[链接](https://example.com)\n条件预测\n' + '全文' * 20000
    calls = []
    def call(q):
        calls.append(q); r = {'ok': True, 'data': {'answer': answer}}
        return r, json.dumps(r)
    h.collect(base, call); h.collect(base, call)
    md, r = h.render(base)
    assert len(calls) == 2 and md.count(answer) == 2 and r['coverage'] == r['success'] == 2
    assert '00700.HK' in ''.join(calls) and '1—3个月' in calls[0] and '+08:00' in calls[0]
    second = tmp_path / 'run2'; h.prepare(second, 'evening', s, '2026-09-18T18:30:00+08:00'); h.collect(second, call)
    assert len(calls) == 4 and h.read(second/'manifest.json')['run_id'] != r['run_id']


def test_isolation_and_business_no_retry(tmp_path):
    base = tmp_path / 'run'; h.prepare(base, 'evening', scope(tmp_path))
    calls = []
    def call(q):
        calls.append(q)
        r = h.error('港股不支持', 'business') if '00700.HK' in q else {'ok': True, 'data': {'answer': '未提供方向/概率'}}
        return r, json.dumps(r)
    h.collect(base, call); md, r = h.render(base)
    assert len(calls) == 2 and r['success'] == 1 and '港股不支持' in md and '未提供方向/概率' in md


def test_network_bounded_retry_and_hard_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr(h.time, 'sleep', lambda _: None)
    base=tmp_path/'run'; h.prepare(base, 'morning', scope(tmp_path))
    calls=[]
    def call(q):
        calls.append(q); r=h.error('超时', 'network', True); return r,json.dumps(r)
    h.collect(base, call)
    assert len(calls)==4
    assert not h.should_retry(h.error('后端返回异常状态码 401','network',True))
    def run(*a, **kw):
        assert kw['timeout']==180
        raise h.subprocess.TimeoutExpired(a[0], 180)
    monkeypatch.setattr(h.subprocess, 'run', run)
    assert h.invoke('q')[0]['error']['category']=='network'


def test_time_scope_and_interrupted_request(tmp_path):
    s=scope(tmp_path); base=tmp_path/'run'
    with pytest.raises(ValueError): h.prepare(base,'morning',s,'2026-09-18T07:15:00')
    m=h.prepare(base,'morning',s,'2026-09-18T07:15:00+08:00')
    with pytest.raises(ValueError): h.prepare(base,'evening',s)
    with pytest.raises(ValueError): h.prepare(base,'morning',s,'2026-09-19T07:15:00+08:00')
    d=base/'00700.HK'; d.mkdir(); h.save(d/'inflight.json',{})
    r=h.collect_one(base,m['stocks'][1],m['asof'],lambda _: pytest.fail('must not duplicate'))
    assert not r['ok'] and '被中断' in r['error']['message']
    with pytest.raises(ValueError): h.stocks({'confirmed_holdings':[{'code':'700.HK','name':'腾讯','market':'HK'}]})


def test_corruption_and_pending_visible(tmp_path):
    base=tmp_path/'run'; m=h.prepare(base,'morning',scope(tmp_path))
    md,r=h.render(base); assert r['success']==0 and md.count('批次未完成')==2
    h.collect(base,lambda _: ({'ok':True,'data':{'answer':'ok'}},'{}'))
    p=base/'00700.HK'/'result.json'; x=h.read(p); x['answer']='changed';h.save(p,x)
    md,r=h.render(base); assert r['success']==1 and 'hash核验失败' in md


@pytest.mark.parametrize('phase',['morning','evening'])
@pytest.mark.parametrize('technical_ok',[True,False])
def test_actual_consumer_preserves_sections_and_hash(tmp_path,monkeypatch,phase,technical_ok):
    from mt1 import action_integration as ai
    from mt1 import ta_pipeline,ta_workflow
    base=tmp_path/'huatai'; h.prepare(base,phase,scope(tmp_path))
    h.collect(base,lambda _: ({'ok':True,'data':{'answer':'# 华泰\n全文\n'}},'{}'))
    monkeypatch.setenv('HUATAI_REPORT_BASE',str(base))
    monkeypatch.setattr(ta_pipeline,'request_refresh',lambda *a: {})
    monkeypatch.setattr(ta_workflow,'report_inbox',lambda *a: {})
    monkeypatch.setattr(ai,'snapshots',lambda *a: [('manifest',{'asof':'2026-09-18','cards':[]})] if technical_ok else [])
    paths=[]
    for name in ('first','second','company'):
        p=tmp_path/(name+'.md');p.write_text('本方'+name);paths.append(p)
    def compose(first,second,company,manifest,out):
        out.write_text('\n\n'.join(p.read_text() for p in (first,second,company))+'\n技术')
        h.save(Path(str(out)+'.receipt.json'),{})
        return {'out':str(out),'sha256':h.sha(out.read_bytes())}
    monkeypatch.setattr(ai,'compose',compose)
    out=tmp_path/'out';r=ai.cycle(phase,tmp_path/'root',out,*paths)
    p=out/('daily.md' if technical_ok else 'base-report-with-warning.md')
    assert all(x.read_text() in p.read_text() for x in paths)
    assert p.read_text().count('# 华泰\n全文\n')==2
    assert r['huatai']['report_sha256']==h.sha(p.read_bytes())
    assert r['huatai']['success']==2
    assert len(r['publication']['parts'])==1
    assert Path(r['publication']['parts'][0]['path']).read_bytes()==p.read_bytes()


def test_publication_lossless_over_document_limit(tmp_path):
    text='# 原日报\n\n' + '|甲|乙|\n' * 20000 + '\n\n' + '正文🙂' * 40000 + '\n链接 https://example.com\n'
    p=tmp_path/'daily.md';p.write_text(text)
    plan=h.publication_parts(p,tmp_path)
    assert len(plan['parts'])>1
    assert ''.join(Path(x['path']).read_text() for x in plan['parts'])==text
    assert all(x['bytes']<=102400 for x in plan['parts'])


def test_two_collectors_only_one_request_per_stock(tmp_path):
    base=tmp_path/'run';h.prepare(base,'morning',scope(tmp_path));calls=[]
    def call(q):
        calls.append(q);r={'ok':True,'data':{'answer':'完整'}};return r,json.dumps(r)
    with h.ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _:h.collect(base,call),range(2)))
    assert len(calls)==2


def test_launcher_uses_independent_budget_and_no_secret_argv(tmp_path,monkeypatch):
    base=tmp_path/'run';s=scope(tmp_path)
    original=h.prepare
    monkeypatch.setattr(h,'prepare',lambda b,p:original(b,p,s))
    monkeypatch.setenv('HT_APIKEY','DO_NOT_LOG_THIS_TEST_KEY')
    calls=[]
    def run(cmd,**kwargs):
        calls.append(cmd)
        assert '--property=RuntimeMaxSec=1950' in cmd
        assert '--setenv=HT_APIKEY' in cmd
        assert not any('DO_NOT_LOG_THIS_TEST_KEY' in x for x in cmd)
        assert kwargs['timeout']==1980
        return type('Result',(),{'returncode':0})()
    monkeypatch.setattr(h.subprocess,'run',run)
    h.start(base,'morning')
    assert len(calls)==1


@pytest.mark.parametrize('size', [90001, 100945, 102399, 102400, 102401])
def test_publication_utf8_boundary(tmp_path, size):
    raw=('中🙂'*(size//7)).encode()+b'x'*(size%7)
    p=tmp_path/'daily.md';p.write_bytes(raw)
    plan=h.publication_parts(p,tmp_path)
    assert len(plan['parts']) == (1 if size<=102400 else 2)
    assert b''.join(Path(x['path']).read_bytes() for x in plan['parts'])==raw
    assert plan['report_sha256']==h.sha(raw)
    assert Path(plan['original_archive']).read_bytes()==raw


@pytest.mark.parametrize('category', h.OPTIONAL_CATEGORIES)
def test_explicit_optional_only_when_needed(tmp_path, category):
    keep='九股结论/风险/出处/MT13 blocked unknown🙂\r\n'
    extra='机器日志哈希回执重复工程记录'*200
    answer='# 华泰服务独立咨询全文\n# 附录（仍是研究，不能删）\n条件与失败状态'+ '中'*100
    raw=(keep+extra+answer).encode()
    p=tmp_path/'daily.md';p.write_bytes(raw)
    a=len(keep.encode());b=a+len(extra.encode())
    spans=[dict(category=category,start=a,end=b,sha256=h.sha(extra))]
    plan=h.publication_parts(p,tmp_path,optional_spans=spans)
    assert plan['omitted']==[] and plan['lossless']
    plan=h.publication_parts(p,tmp_path,limit=1024,optional_spans=spans)
    assert plan['omitted']==spans and not plan['lossless']
    assert Path(plan['parts'][0]['path']).read_bytes()==(keep+answer).encode()
    assert p.read_bytes()==raw
    plan=h.publication_parts(p,tmp_path,limit=128,optional_spans=spans)
    assert plan['fallback_reason'] and len(plan['parts'])>1
    assert b''.join(Path(x['path']).read_bytes() for x in plan['parts'])==(keep+answer).encode()


def test_cannot_label_huatai_as_optional(tmp_path):
    raw='# 华泰服务独立咨询全文\n附录 日志 风险和预测'.encode()
    p=tmp_path/'daily.md';p.write_bytes(raw)
    with pytest.raises(ValueError,match='invalid_optional'):
        h.publication_parts(p,tmp_path,limit=40,optional_spans=[
            dict(category='duplicate_engineering_appendix', start=0,end=len(raw),sha256=h.sha(raw))])


def test_no_heading_heuristics_or_stale_spans(tmp_path):
    raw=('## 执行日志 附录\n仍是本方研究与失败状态🙂'*5000).encode()
    p=tmp_path/'daily.md';p.write_bytes(raw)
    plan=h.publication_parts(p,tmp_path)
    assert plan['lossless'] and not plan['omitted'] and plan['fallback_reason']
    with pytest.raises(ValueError):
        h.publication_parts(p,tmp_path,optional_spans=[dict(category='hash',start=0,end=3,sha256='wrong')])


def test_real_cycle_passes_optional_spans_after_huatai_prefix(tmp_path, monkeypatch):
    from mt1 import action_integration as ai, ta_pipeline, ta_workflow
    monkeypatch.setattr(ta_pipeline,'request_refresh',lambda *a: {})
    monkeypatch.setattr(ta_workflow,'report_inbox',lambda *a: {})
    monkeypatch.setattr(ai,'snapshots',lambda *a: [('manifest',{'asof':'2026-09-21','cards':[]})])
    monkeypatch.setenv('HUATAI_REPORT_BASE','unused-no-request')
    ht='\n\n# 华泰服务独立咨询全文\n预测/出处/限定条件/失败状态'
    monkeypatch.setattr(h,'render',lambda *a: (ht,{'success':1}))
    keep='本方九股风险🙂'+'x'*102200
    extra='执行日志'*100
    def compose(first,second,company,manifest,out):
        out.write_text(keep+extra)
        a=len(keep.encode());b=len((keep+extra).encode())
        return {'out':str(out),'publication_optional_spans':[
            dict(category='execution_log',start=a,end=b,sha256=h.sha(extra))]}
    monkeypatch.setattr(ai,'compose',compose)
    r=ai.cycle('morning',tmp_path,tmp_path/'out',tmp_path/'first',tmp_path/'second',tmp_path/'company')
    plan=r['publication']
    assert len(plan['parts'])==1 and plan['omitted']
    assert Path(plan['parts'][0]['path']).read_text()=='# 本方日报判断与 TA/MT13\n\n'+keep+ht
