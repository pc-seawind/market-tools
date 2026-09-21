"""Huatai skill consultation: immutable per-report requests, bounded isolated workers."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

SCOPE = Path('/home/emox/work/investment/reference/tracking-scope.json')
SKILL = Path('/home/emox/.homespace/skills/financial-analysis/financial_analysis.py')
TIMEOUT = 180


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(value):
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


def save(path, value):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2) + '\n'
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(text)
    os.replace(tmp, path)


def read(path):
    return json.loads(path.read_text())


def stocks(scope):
    result = {}
    for key in ('confirmed_holdings', 'active_candidates', 'active_recommendations'):
        for s in scope.get(key, []):
            code = s['code']
            if not re.fullmatch(r'(?:\d{6}\.(?:SH|SZ)|\d{5}\.HK)', code):
                raise ValueError('unsupported_scope_code:' + code)
            if s['market'] != ('HK' if code.endswith('.HK') else 'CN'):
                raise ValueError('scope_market_mismatch:' + code)
            result[code] = {k: s[k] for k in ('code', 'name', 'market')}
    if not result:
        raise ValueError('empty_scope')
    return list(result.values())


def query(s, asof):
    return (f"请对{s['market']}市场 {s['code']} {s['name']} 做独立形势分析和走势预测。"
            f"报告截至时点为 {asof}，不得使用该时点之后信息。请注明实际资料截至日期及信息局限。"
            "请完整分析当前形势、关键驱动与风险、市场预期及分歧；分别给出短期（未来1—10个交易日）"
            "与中期（未来1—3个月）走势方向、推演链、基准/乐观/悲观情景、触发因素及失效条件。"
            "允许有依据的条件预测，不因预测不是既成事实而回避观点；事实、外部观点和推演请区分。"
            "无依据的方向或概率请明确未提供，不编造概率、目标价或行情。港股若不支持请直说。"
            "请保留引用来源链接、标题、表格及限定条件，完整返回分析。")


def error(message, category='local', retriable=False):
    return {'ok': False, 'data': None, 'error': {'message': message, 'category': category, 'retriable': retriable}}


def invoke(q):
    try:
        p = subprocess.run([sys.executable, str(SKILL), 'marketInsight', '--query', q],
                           capture_output=True, text=True, timeout=TIMEOUT)
        # Do not persist stderr: third-party libraries can include credentials there.
        raw = p.stdout
        r = json.loads(raw)
        if not isinstance(r, dict):
            raise ValueError('response_not_object')
        return r, raw
    except subprocess.TimeoutExpired:
        r = error('单请求硬超时（180秒）', 'network', True)
    except Exception as e:
        r = error('调用失败:' + type(e).__name__)
    return r, json.dumps(r, ensure_ascii=False)


def should_retry(r):
    e = r.get('error') or {}
    # Skill labels HTTP 4xx as network too. Never retry auth/validation 4xx.
    return (e.get('category') == 'network' and e.get('retriable') is True
            and not re.search(r'状态码 4\d\d', e.get('message', '')))


def collect_one(base, stock, asof, call=invoke):
    directory = base / stock['code']
    directory.mkdir(exist_ok=True)
    final = directory / 'result.json'
    if final.exists():
        return read(final)
    q = query(stock, asof)
    save(directory / 'request.json', {'stock': stock, 'asof': asof, 'query': q, 'sha256': sha(q)})
    attempts = []
    # Interrupted requests are unknown, not safe to duplicate automatically.
    if (directory / 'inflight.json').exists():
        r = error('上次请求被中断，服务是否计费未知；同报告不自动重复咨询')
    else:
        for i in range(2):
            started_at = now()
            save(directory / 'inflight.json', {'attempt': i + 1, 'started_at': started_at})
            try:
                r, raw = call(q)
            except Exception as e:
                r = error('单股执行异常:' + type(e).__name__)
                raw = json.dumps(r, ensure_ascii=False)
            stamp = now()
            save(directory / f'response-{i+1}.json', raw)
            attempts.append({'attempt': i + 1, 'started_at': started_at, 'fetched_at': stamp, 'sha256': sha(raw),
                             'path': str(directory / f'response-{i+1}.json')})
            if not should_retry(r) or i == 1:
                break
            time.sleep(1)
    answer = (r.get('data') or {}).get('answer')
    ok = r.get('ok') is True and isinstance(answer, str) and bool(answer.strip())
    result = {'stock': stock, 'asof': asof, 'fetched_at': now(), 'attempts': attempts,
              'ok': ok, 'answer': answer if ok else '', 'answer_sha256': sha(answer) if ok else None,
              'service_date': None, 'service_date_note': 'skill不暴露独立服务日期字段；正文日期原样保留，不以抓取时刻冒充服务日期',
              'error': None if ok else (r.get('error') or {'message': '服务未提供有效正文'})}
    save(final, result)
    return result


def collect(base, call=invoke):
    base = Path(base)
    with (base / '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        manifest = read(base / 'manifest.json')
        if (base / 'complete.json').exists():
            return read(base / 'complete.json')
        with ThreadPoolExecutor(max_workers=2) as pool:
            rows = list(pool.map(lambda s: collect_one(base, s, manifest['asof'], call), manifest['stocks']))
        result = {'run_id': manifest['run_id'], 'completed_at': now(), 'coverage': len(rows),
                  'success': sum(r['ok'] for r in rows), 'results': rows, 'fees': '未知；服务未返回费用字段'}
        save(base / 'complete.json', result)
        return result


def prepare(base, phase, scope=SCOPE, asof=None):
    base = Path(base); base.mkdir(parents=True, exist_ok=True)
    with (base / '.prepare.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        raw = Path(scope).read_bytes()
        path = base / 'manifest.json'
        if path.exists():
            m = read(path)
            if m['scope_sha256'] != sha(raw) or m['phase'] != phase:
                raise ValueError('report_identity_changed_use_new_report_directory')
            if asof and m['asof'] != asof:
                raise ValueError('report_asof_changed_use_new_report_directory')
            return m
        asof = asof or now()
        if datetime.fromisoformat(asof).tzinfo is None:
            raise ValueError('asof_timezone_required')
        m = {'run_id': sha(str(base.resolve()) + phase + asof)[:24], 'phase': phase, 'asof': asof,
             'scope_path': str(scope), 'scope_sha256': sha(raw), 'stocks': stocks(json.loads(raw)),
             'skill_sha256': sha(SKILL.read_bytes()), 'created_at': now()}
        save(base / 'scope.json', raw.decode()); save(path, m)
        return m


def render(base):
    base = Path(base); m = read(base / 'manifest.json')
    if sha(Path(m['scope_path']).read_bytes()) != m['scope_sha256']:
        raise ValueError('scope_changed_refuse_old_consultation')
    rows = []
    for s in m['stocks']:
        p = base / s['code'] / 'result.json'
        r = read(p) if p.exists() else {'stock': s, 'ok': False, 'error': {'message': '批次未完成/硬超时/启动失败，未取得返回'}}
        valid = True
        if p.exists():
            valid = r['stock'] == s and r['asof'] == m['asof']
            for attempt in r['attempts']:
                raw = Path(attempt['path'])
                valid = valid and raw.exists() and sha(raw.read_bytes()) == attempt['sha256']
            req = read(base / s['code'] / 'request.json')
            valid = valid and req['query'] == query(s, m['asof']) and req['sha256'] == sha(req['query'])
        if not valid or (r['ok'] and sha(r['answer']) != r['answer_sha256']):
            r = {'stock': s, 'ok': False, 'error': {'message': '正文hash核验失败，原档保留'}}
        rows.append(r)
    text = '\n\n# 华泰服务独立咨询全文\n\n'
    text += ('以下为华泰 financial-analysis API 服务返回，不是人工分析师签署研报。与上文本方判断、TA/MT13独立，观点不替代交易决策。'
             '不补造来源未给出的方向、概率或目标价；正文是否覆盖全部预测期限请以原文为准。\n\n')
    text += f"咨询 run：{m['run_id']}；截至：{m['asof']}。服务独立日期字段未提供，正文资料日期原样保留。费用未知。\n"
    for r in rows:
        s = r['stock']; text += f"\n\n## {s['name']} · {s['code']} · {s['market']}\n\n"
        if r['ok']:
            text += f"抓取：{r['fetched_at']}；正文 SHA256：{r['answer_sha256']}\n\n"
            text += '预测方向与概率：仅以以下服务原文为准；未明确给出的项目即未提供，本系统不推断补齐。\n\n'
            text += r['answer']  # Deliberately no strip/summary/TA fact gate.
        else:
            text += '本股咨询失败：' + json.dumps(r['error'], ensure_ascii=False)
    receipt = {'run_id': m['run_id'], 'asof': m['asof'], 'scope_sha256': m['scope_sha256'],
               'coverage': len(rows), 'success': sum(r['ok'] for r in rows), 'full_text': True,
               'stocks': [{'code': r['stock']['code'], 'ok': r['ok'], 'answer_sha256': r.get('answer_sha256'),
                           'error': r.get('error')} for r in rows], 'archive': str(base)}
    return text, receipt


PUBLICATION_LIMIT_BYTES = 100 * 1024  # worker source_file AND gateway UTF-8 cap
OPTIONAL_CATEGORIES = ('execution_log', 'hash', 'consumer_receipt', 'duplicate_engineering_appendix')


def publication_parts(report, out, limit=PUBLICATION_LIMIT_BYTES, optional_spans=()):
    """Prefer one doc; only producer-declared engineering byte ranges are optional.

    No heading/length-based research deletion. Undeclared legacy content stays.
    Huatai's entire section is protected, including conditions and failure status.
    """
    if not 4 <= limit <= PUBLICATION_LIMIT_BYTES:
        raise ValueError('invalid_publication_limit')
    raw = Path(report).read_bytes()
    raw.decode('utf-8')  # validate without normalizing CRLF
    protected = raw.find('# 华泰服务独立咨询全文'.encode())
    if protected < 0:
        protected = len(raw)
    candidates = sorted(optional_spans, key=lambda x: x['start'])
    previous = 0
    for span in candidates:
        a, b = span['start'], span['end']
        if (span['category'] not in OPTIONAL_CATEGORIES or
                not previous <= a < b <= protected or sha(raw[a:b]) != span['sha256']):
            raise ValueError('invalid_optional_publication_span')
        raw[:a].decode('utf-8'); raw[a:b].decode('utf-8'); raw[b:].decode('utf-8')
        previous = b
    omitted = []
    remaining = len(raw)
    for category in OPTIONAL_CATEGORIES:
        for span in candidates:
            if remaining <= limit:
                break
            if span['category'] == category:
                omitted.append(dict(span))
                remaining -= span['end'] - span['start']
    selected = sorted(omitted, key=lambda x: x['start'])
    chunks, cursor = [], 0
    for span in selected:
        chunks.append(raw[cursor:span['start']]); cursor = span['end']
    chunks.append(raw[cursor:])
    text = b''.join(chunks).decode('utf-8')
    # Keep a local immutable-content snapshot even if the caller later moves its run.
    Path(out).mkdir(parents=True, exist_ok=True)
    archive = Path(out) / ('publication-original-' + sha(raw) + '.md')
    if archive.exists() and archive.read_bytes() != raw:
        raise ValueError('publication_archive_changed')
    if not archive.exists():
        archive.write_bytes(raw)
    parts, current = [], ''
    # Prefer paragraph boundaries; preserve every character, including tables/links.
    for paragraph in re.split(r'(?<=\n\n)', text):
        if len(paragraph.encode()) > limit:
            # Exceptional giant paragraph: retain all text across bounded pieces.
            units = paragraph.splitlines(keepends=True)
        else:
            units = [paragraph]
        for unit in units:
            while len(unit.encode()) > limit:
                if current:
                    parts.append(current); current = ''
                n = limit // 4  # safe UTF-8 boundary even for 4-byte characters
                parts.append(unit[:n]); unit = unit[n:]
            if len((current + unit).encode()) > limit:
                parts.append(current); current = ''
            current += unit
    if current: parts.append(current)
    assert ''.join(parts) == text
    entries = []
    for i, content in enumerate(parts, 1):
        p = Path(out) / f'publication-part-{i:02}.md'
        save(p, content)
        entries.append({'path': str(p), 'sha256': sha(content), 'bytes': len(content.encode())})
    plan = {'report_sha256': sha(raw), 'published_sha256': sha(text),
            'source_bytes': len(raw), 'limit_bytes': limit,
            'original_archive': str(archive), 'omitted': omitted,
            'lossless': not omitted, 'parts': entries,
            'fallback_reason': ('retained_content_exceeds_limit_after_safe_omissions' if len(parts) > 1 else None),
            'delivery': '优先单文档；多份时说明保留正文超限，逐份hs_create_doc并给全部裸URL；省略清单及原稿仅本地归档；保存真实回执并readback',
            'not_published': True}
    save(Path(out) / 'publication-parts.json', plan)
    return plan


def start(base, phase):
    m = prepare(base, phase)
    if (Path(base) / 'complete.json').exists():
        return
    budget = max(1950, ((len(m['stocks']) + 1) // 2) * (2 * TIMEOUT + 5) + 60)
    cmd = ['systemd-run', '--user', '--wait', '--collect', '--unit=huatai-' + m['run_id'],
           '--property=RuntimeMaxSec=' + str(budget), '--property=TimeoutStopSec=5',
           '--working-directory=' + str(Path(__file__).resolve().parents[1]),
           sys.executable, '-m', 'mt1.huatai_daily', 'collect', '--base', str(Path(base).resolve())]
    # Pass environment variable NAMES only; systemd-run obtains their values itself.
    # File configuration remains the installed skill's fallback. Never put keys in argv/logs.
    insertion = cmd.index(sys.executable)
    names = ('HT_APIKEY', 'FINANCIAL_ANALYSIS_SERVICE_URL', 'FINANCIAL_ANALYSIS_BASE_URL',
             'HTTP_PROXY', 'HTTPS_PROXY', 'NO_PROXY')
    cmd[insertion:insertion] = ['--setenv=' + name for name in names if name in os.environ]
    p = subprocess.run(cmd, timeout=budget + 30, capture_output=True, text=True)
    save(Path(base) / ('launch-' + str(time.time_ns()) + '.json'), {'returncode': p.returncode, 'at': now()})


def main():
    p = argparse.ArgumentParser(); p.add_argument('action', choices=['start', 'collect'])
    p.add_argument('--base', required=True); p.add_argument('--phase', choices=['morning', 'evening'])
    a = p.parse_args()
    if a.action == 'start': start(a.base, a.phase)
    else: collect(a.base)


if __name__ == '__main__':
    main()
