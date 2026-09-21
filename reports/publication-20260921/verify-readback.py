"""Run with uv run --no-project --with markdown-it-py python verify-readback.py."""
import json
from pathlib import Path
from urllib.parse import unquote
from markdown_it import MarkdownIt
base=Path(__file__).resolve().parent
parser=MarkdownIt('commonmark').enable('table')
def plain(md):
    return ''.join(t.content for x in parser.parse(md) for t in (x.children or [])
                   if t.type in ('text','code_inline','html_inline'))
def norm(s):
    return ''.join(s.split())  # formatting whitespace ONLY; numbers/punctuation retained
src=(base/'publication-part-01.md').read_text()
raw=json.loads((base/'feishu-raw-content.json').read_text())
blocks=json.loads((base/'feishu-blocks.json').read_text())
assert raw['code']==blocks['code']==0 and not blocks['data']['has_more']
body=raw['data']['content'].split('\n',1)[1]
assert norm(plain(src))==norm(body)
links=[]
def visit(x):
    if isinstance(x,dict):
        if 'link' in x: links.append(unquote(x['link']['url']))
        for v in x.values():visit(v)
    elif isinstance(x,list):
        for v in x:visit(v)
visit(blocks)
expected=[t.attrGet('href') for x in parser.parse(src) for t in (x.children or []) if t.type=='link_open']
assert all(url in links for url in expected)
archive=Path('/home/emox/work/investment/reference/daily-reports/20260921T071500-morning/huatai-morning')
checks=[]
for p in sorted(archive.glob('*/result.json')):
    r=json.loads(p.read_text());assert r['ok'] and r['answer'] in src
    assert norm(plain(r['answer'])) in norm(body)
    checks.append(r['stock']['code'])
assert len(checks)==9
result={'url':'https://tcnv6xag1i9w.feishu.cn/docx/K6CUdMql0oABpfxtR96ceo02nIg',
        'full_rendered_text_equal_ignoring_whitespace_only':True,'rendered_chars':len(norm(body)),
        'huatai_verbatim_source_and_rendered_coverage':checks,'markdown_links_verified':len(expected),
        'note':'原稿逐字保留；云端Markdown呈现去结构标记，仅空白归一后全篇逐字符一致。MCP回读大响应失败，改只读分页API核验。'}
(base/'readback-verification.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(result,ensure_ascii=False))
