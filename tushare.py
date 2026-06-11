#!/usr/bin/env python3
"""tushare.py — thin REST wrapper for tushare Pro (https://tushare.pro).

This is the generic escape hatch — any tushare API endpoint can be called
by name. For common OHLCV use cases, prefer the higher-level `history.sh`
wrapper which handles ticker-shape routing and date defaults.

Usage:
    tushare.py <api_name> [key=value ...] [--fields=a,b,c] [--csv]

Examples:
    # A-share daily OHLCV
    tushare.py daily ts_code=600519.SH start_date=20250101 end_date=20250501 \\
               --fields=trade_date,open,high,low,close,vol --csv

    # Index daily (e.g. CSI 300)
    tushare.py index_daily ts_code=000300.SH start_date=20250101 --csv

    # Stock basic info
    tushare.py stock_basic exchange=SSE list_status=L --fields=ts_code,name

Environment:
    TUSHARE_TOKEN — required. Register at https://tushare.pro to get one.

Output:
    Default: pretty-printed JSON of the full response body.
    With --csv: only the data rows, with a header line.

Exit codes:
    0  ok
    2  bad usage
    3  missing TUSHARE_TOKEN
    4  network / HTTP error
    5  tushare-reported error (non-zero code in response)

Dependencies: python3 stdlib only (urllib, json). No pip install needed.
"""

import datetime
import hashlib
import json
import os
import ssl
import sys
import tempfile
import time
import urllib.error
import urllib.request

# 2026-05-20: tushare.pro 服务端 SSL cert 当天过期 (notAfter=May 19 23:59:59 GMT).
# 提供环境变量 TUSHARE_INSECURE_SSL=1 临时绕过验证, 让早盘 cron 不至于挂.
# 一旦 tushare 续期, 应当 unset 此环境变量恢复严格验证.
if os.environ.get("TUSHARE_INSECURE_SSL") == "1":
    ssl._create_default_https_context = ssl._create_unverified_context

# 加载可选扩展模块 (都是软依赖, 失败不影响 tushare.py 核心)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Parquet 持久化层 (需 duckdb + pyarrow)
try:
    import cache_parquet as _parquet
except Exception:
    _parquet = None

# akshare fallback (当 tushare 40203 配额超限时尝试用 akshare 拿数据).
# 目前覆盖: hk_daily, daily, cctv_news. 其他 API fallback=None.
try:
    import akshare_fallback as _akshare_fb
except Exception:
    _akshare_fb = None

ENDPOINT = "https://api.tushare.pro"
TIMEOUT = 15

# 本地 JSON 缓存, 应对 tushare 各 API 的配额限制 (e.g. hk_daily 10/day,
# sw_daily 10/min, cctv_news 5/min). 缓存 key = hash(api_name + params + fields).
# TTL 按 API 类别区分 (见 _cache_ttl).
#
# 环境变量:
#   TUSHARE_CACHE_DIR     缓存目录 (默认 ~/.homespace/cache/market-tools)
#   TUSHARE_NO_CACHE=1    禁用缓存 (每次都调 API)
#   TUSHARE_CACHE_DEBUG=1 打印 cache hit/miss/write 日志到 stderr
CACHE_DIR = os.environ.get(
    "TUSHARE_CACHE_DIR",
    os.path.expanduser("~/.homespace/cache/market-tools"),
)
_CACHE_DEBUG = os.environ.get("TUSHARE_CACHE_DEBUG") == "1"
_CACHE_DISABLED = os.environ.get("TUSHARE_NO_CACHE") == "1"


def _is_no_permission_msg(msg: str) -> bool:
    """Return True for Tushare permission/subscription errors.

    Tushare sometimes reports API-level no-permission as code=40203
    (the same code used for frequency limits).  Retrying those wastes
    35+45+55s per unique query, which can make cron jobs time out.
    """
    m = (msg or "").lower()
    needles = (
        "没有接口",
        "没有权限",
        "无权限",
        "访问权限",
        "no permission",
        "permission denied",
        "not authorized",
        "unauthorized",
    )
    return any(x in m for x in needles)


# API 类别 (决定 TTL 策略)
# permanent-by-date: 如果 params 含历史日期, 永久缓存 (已收盘数据不会变)
_PERMANENT_BY_DATE_APIS = {
    "daily", "hk_daily", "sw_daily", "cctv_news", "daily_basic",
    "index_daily", "bak_daily", "moneyflow_ind_ths", "moneyflow_ind_dc",
    "adj_factor",
}
# 长期静态 (月度刷新)
_STATIC_APIS = {
    "stock_basic", "trade_cal", "hk_tradecal", "hk_basic",
    "index_classify", "index_basic",
    "ths_index",   # 板块列表
}
# 成员/持仓数据 (半月-月刷新)
_SEMI_STATIC_APIS = {
    "index_member", "ths_member", "dc_member",
}
# 财报 (季度性刷新)
_QUARTERLY_APIS = {
    "fina_indicator", "forecast", "express", "income", "balancesheet",
    "cashflow", "fina_audit", "fina_mainbz",
}
# 持股/资金流 (周-日刷新)
_WEEKLY_APIS = {
    "top10_holders", "top10_floatholders",
}
_DAILY_APIS = {
    "moneyflow_hsgt", "hsgt_top10", "top_list", "top_inst",
}
# 基金日级 (14h TTL — 避免 cron 间隔 ≈ TTL 的竞态条件:
#   每日 08:00 写入的缓存在 22:00 过期, 次日 08:00 必 miss → 拿新数据)
_FUND_DAILY_APIS = {
    "fund_daily", "fund_share", "fund_adj",
}


def _cache_key(api_name, params, fields):
    payload = {"api": api_name, "params": params or {}, "fields": fields or ""}
    return hashlib.md5(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _cache_ttl(api_name, params):
    """返回 TTL (秒), None = 永不过期."""
    today_str = datetime.date.today().strftime("%Y%m%d")

    if api_name in _PERMANENT_BY_DATE_APIS:
        # 历史日期永久; 当日 30 min (盘中数据可能未齐)
        td = params.get("trade_date") or params.get("date")
        if td:
            return None if td < today_str else 1800
        # 日期范围: end_date < today → 历史永久
        end = params.get("end_date")
        if end:
            return None if end < today_str else 1800
        # 无日期参数 (e.g. ts_code + 某只股拉全量 K 线)
        # 14h TTL: 每天都有新K线追加, 同 fund_daily 防竞态逻辑
        return 50400  # 14h

    if api_name in _STATIC_APIS:
        return 86400 * 30
    if api_name in _SEMI_STATIC_APIS:
        return 86400 * 14
    if api_name in _QUARTERLY_APIS:
        return 86400 * 7    # 季度性变化, 1 周粒度够
    if api_name in _WEEKLY_APIS:
        return 86400 * 7
    if api_name in _DAILY_APIS:
        return 86400
    if api_name in _FUND_DAILY_APIS:
        return 50400  # 14h — 防 cron 间隔 ≈ TTL 竞态

    return 86400  # 默认 1 天


def _cache_read(api_name, params, fields):
    if _CACHE_DISABLED:
        return None
    key = _cache_key(api_name, params, fields)
    cache_file = os.path.join(CACHE_DIR, api_name, f"{key}.json")
    if not os.path.exists(cache_file):
        if _CACHE_DEBUG: sys.stderr.write(f"[cache-miss] {api_name} {params}\n")
        return None
    age = time.time() - os.path.getmtime(cache_file)
    try:
        with open(cache_file, "r", encoding="utf-8") as f:
            body = json.load(f)
    except Exception as e:
        if _CACHE_DEBUG: sys.stderr.write(f"[cache-err]  read {cache_file}: {e}\n")
        return None

    # 判断 TTL: negative cache 用自己的 _neg_ttl, positive 用 _cache_ttl(api, params)
    neg_ttl = body.pop("_neg_ttl", None) if isinstance(body, dict) else None
    if neg_ttl is not None:
        ttl = neg_ttl
        tag = "NEG"
    else:
        ttl = _cache_ttl(api_name, params)
        tag = "POS"

    if ttl is not None and age >= ttl:
        if _CACHE_DEBUG:
            sys.stderr.write(f"[cache-stale] {api_name} ({tag}) age={age:.0f}s ttl={ttl}s\n")
        return None

    if _CACHE_DEBUG:
        ttl_str = "∞" if ttl is None else f"{ttl}s"
        sys.stderr.write(f"[cache-hit]  {api_name} ({tag}) age={age:.0f}s ttl={ttl_str}\n")
    return body


def _cache_write(api_name, params, fields, body):
    """写缓存. 成功响应按正常 TTL; 错误响应按 negative TTL (避免重复撞墙)."""
    if _CACHE_DISABLED:
        return
    if not isinstance(body, dict):
        return

    code = body.get("code", 0)
    data = body.get("data") or {}

    # 负缓存策略 (避免已知错误的 retry 浪费):
    # - code 40203 (配额/限速)  → 1h  (等窗口重置)
    # - code 40202 (无权限)    → 1d  (权限稳定)
    # - code == 0 且 items 空  → 1h  (日期非交易日/无数据)
    # - 其他 error             → 不缓存
    neg_ttl = None
    msg = body.get("msg") or ""
    if code == 40203 and _is_no_permission_msg(msg):
        neg_ttl = 86400          # 1d (API permission is stable; do not retry hourly)
    elif code == 40203:
        neg_ttl = 3600           # 1h
    elif code == 40202:
        neg_ttl = 86400          # 1d
    elif code == 0 and not data.get("items"):
        neg_ttl = 3600           # 1h (空结果)
    elif code != 0:
        return                   # 其他错误不缓存

    key = _cache_key(api_name, params, fields)
    cache_subdir = os.path.join(CACHE_DIR, api_name)
    try:
        os.makedirs(cache_subdir, exist_ok=True)
        cache_file = os.path.join(cache_subdir, f"{key}.json")
        # 包装: 如果是 negative cache, 追加 _neg_ttl 标记, _cache_read 会按此判断过期
        to_write = dict(body)
        if neg_ttl is not None:
            to_write["_neg_ttl"] = neg_ttl
        # atomic write (temp + rename), 防止并发写损坏
        fd, tmp_path = tempfile.mkstemp(dir=cache_subdir, prefix=".tmp-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(to_write, f, ensure_ascii=False)
            os.replace(tmp_path, cache_file)
            if _CACHE_DEBUG:
                rows = len(data.get("items") or [])
                tag = f"NEG ttl={neg_ttl}s" if neg_ttl else f"{rows} rows"
                sys.stderr.write(f"[cache-write] {api_name} {tag}\n")
        except Exception:
            try: os.unlink(tmp_path)
            except Exception: pass
            raise
    except Exception as e:
        if _CACHE_DEBUG: sys.stderr.write(f"[cache-err]  write {api_name}: {e}\n")

# Some shells have a stale SOCKS/HTTP proxy in HTTPS_PROXY/ALL_PROXY pointing at
# a dead localhost port — this would trip urllib.urlopen before it ever reaches
# tushare. tushare's endpoint is a public Aliyun SLB and always directly
# reachable, so we install an opener that ignores env proxies. Set
# TUSHARE_USE_ENV_PROXY=1 to override (e.g. if you're actually behind a proxy
# that's the only path to the public internet).
if os.environ.get("TUSHARE_USE_ENV_PROXY") != "1":
    _no_proxy = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    urllib.request.install_opener(_no_proxy)


def usage():
    sys.stderr.write(__doc__)


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        usage()
        return 2

    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        sys.stderr.write("ERROR: TUSHARE_TOKEN env var not set\n")
        return 3

    api_name = argv[0]
    params = {}
    fields = ""
    out_csv = False

    for arg in argv[1:]:
        if arg == "--csv":
            out_csv = True
        elif arg == "--no-cache":
            pass   # 已在外层检测 --no-cache in argv
        elif arg.startswith("--fields="):
            fields = arg[len("--fields="):]
        elif "=" in arg:
            k, v = arg.split("=", 1)
            params[k] = v
        else:
            sys.stderr.write(f"bad arg: {arg!r} (expected key=value or --flag)\n")
            return 2

    # 先查缓存 (除非 --no-cache 或 TUSHARE_NO_CACHE=1)
    no_cache_flag = "--no-cache" in argv
    body = None
    from_cache = False
    if not no_cache_flag:
        body = _cache_read(api_name, params, fields)
        if body is not None:
            from_cache = True

    payload = {
        "api_name": api_name,
        "token": token,
        "params": params,
        "fields": fields,
    }

    req = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )

    # Tushare rate-limits per-API (e.g. sw_daily: 10/min). On 40203 we
    # transparently sleep + retry rather than bubbling the error, so batch
    # scripts don't have to manage windows. Up to 3 retries (~2 min max wait).
    # Set TUSHARE_NO_RETRY=1 to opt out.
    no_retry = os.environ.get("TUSHARE_NO_RETRY") == "1"
    max_retries = 0 if no_retry else 3

    # 如果缓存命中, 跳过 HTTP 调用, 直接 render
    if body is not None:
        max_retries = -1  # skip loop

    for attempt in range(max_retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            snippet = e.read().decode("utf-8", errors="replace")[:300]
            sys.stderr.write(f"HTTP {e.code} from tushare: {snippet}\n")
            return 4
        except Exception as e:
            sys.stderr.write(f"request failed: {e}\n")
            return 4

        code = body.get("code", 0)
        if code != 40203 or attempt == max_retries:
            break

        # 40203 = frequency limit. Tushare 的错误信息区分"次/分钟"(分钟级)
        # 和"次/天"(天级, e.g. hk_daily 10/day). 分钟级 retry 有效 (等窗口重置),
        # 天级 retry 无用 (等到明天), 直接 bail 并让上层写 negative cache.
        msg = body.get("msg") or ""
        if _is_no_permission_msg(msg):
            sys.stderr.write(
                f"tushare no-permission ({api_name}): {msg.strip()}  "
                f"skip retry (API permission/subscription error)\n"
            )
            break

        if "次/天" in msg or "/day" in msg.lower() or "per day" in msg.lower():
            sys.stderr.write(
                f"tushare day-quota exceeded ({api_name}): {msg.strip()}  "
                f"skip retry (day-level quota, 等配额重置)\n"
            )
            break

        # 分钟级限速: backoff 35-55s retry
        wait = 35 + attempt * 10
        sys.stderr.write(
            f"tushare rate-limited ({api_name}): {msg.strip()}  "
            f"retry {attempt + 1}/{max_retries} after {wait}s...\n"
        )
        time.sleep(wait)

    # akshare fallback: tushare 配额超限 (40203) 时尝试从 akshare 拿.
    # 成功的话 body 被替换为 akshare 结果 (code=0), 后续缓存写入 positive.
    # 失败 (akshare 没对应 API 或也失败) 则保留 tushare 的 40203 走 negative cache.
    if (not from_cache and _akshare_fb is not None
            and isinstance(body, dict) and body.get("code") == 40203):
        if _akshare_fb.available_for(api_name):
            try:
                fb_body = _akshare_fb.fallback(api_name, params, fields)
                if fb_body is not None:
                    sys.stderr.write(f"[akshare fallback] {api_name} OK, "
                                     f"{len(fb_body.get('data', {}).get('items', []))} rows\n")
                    body = fb_body
                else:
                    sys.stderr.write(f"[akshare fallback] {api_name} returned None\n")
            except Exception as e:
                sys.stderr.write(f"[akshare fallback] {api_name} error: {e}\n")

    # 写缓存要在 "return 5 错误" 之前 — 错误响应也需要 negative cache
    # 避免下次重复撞墙 (e.g. hk_daily 10/day 配额超限应 1h 内不再 retry)
    if not no_cache_flag and not from_cache:
        _cache_write(api_name, params, fields, body)

    # 同时写入 Parquet 持久化层 (仅成功响应, 仅配置了 schema 的 API)
    # 独立于 JSON cache: JSON 是"查询级"缓存, Parquet 是"数据级"持久化.
    # 失败静默, 不影响主流程.
    if (not no_cache_flag and not from_cache
            and _parquet is not None
            and isinstance(body, dict) and body.get("code", 0) == 0):
        try:
            if _parquet.enabled_for(api_name):
                # 注入 params 里有但 response 可能缺的 key 字段
                # (tushare 按 ts_code 查询时可能不返回 ts_code 列; 按
                # trade_date 查询 + fields 限定时可能不返回 trade_date 列)
                inject = {}
                for k in ("ts_code", "trade_date", "date",
                          "end_date", "index_code"):
                    if k in params:
                        inject[k] = params[k]
                rows = _parquet.body_to_rows(body, inject=inject)
                if rows:
                    _parquet.append(api_name, rows)
        except Exception as e:
            if _CACHE_DEBUG:
                sys.stderr.write(f"[parquet-err] {api_name}: {e}\n")

    code = body.get("code", 0) if body else -1
    if code != 0:
        msg = body.get("msg") if body else "empty body"
        sys.stderr.write(f"tushare error (code={code}): {msg}\n")
        return 5

    data = body.get("data") or {}

    if out_csv:
        fields_list = data.get("fields") or []
        items = data.get("items") or []
        print(",".join(fields_list))
        for row in items:
            print(",".join(
                "" if x is None else str(x)
                for x in row
            ))
    else:
        print(json.dumps(body, ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
