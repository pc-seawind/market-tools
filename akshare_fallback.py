"""akshare_fallback.py — akshare 作为 tushare 配额超限的 fallback 数据源.

为什么需要:
  tushare 各 API 有不同配额 (hk_daily 10/day, cctv_news 5/min, sw_daily 10/min,
  等). 配额撞墙时当前做法是 negative cache + 等配额重置, 但有时(尤其是 HK 每日
  只有 10 次) 数据拿不到就是拿不到. akshare 完全免费, 不需 token, 可以绕开
  tushare 限制.

策略:
  tushare 为 primary (权威 + 结构化), akshare 为 fallback (当 tushare 40203
  配额超限时触发). Response 格式归一化为 tushare body shape, 上层工具无感知.

覆盖的 API (初期):
  hk_daily      → ak.stock_hk_hist       (港股日线, 最痛的场景)
  daily         → ak.stock_zh_a_hist     (A 股日线)
  cctv_news     → ak.news_cctv           (新闻联播)
  其他 API 暂无对应, fallback 返回 None 交回 tushare 的 negative cache

单位对齐:
  akshare "成交额" 单位是元, tushare daily "amount" 是千元, 转换 / 1000.
  akshare "涨跌幅" 是百分数 (e.g. 1.11), tushare "pct_chg" 也是百分数, 一致.
  akshare "日期" 是 "YYYY-MM-DD", tushare "trade_date" 是 "YYYYMMDD", 去 "-".
"""

import os
import sys

# 清掉环境代理 (和 tushare.py 一致的理由: 避免 stale SOCKS 127.0.0.1 截断
# akshare 内部 requests 调用). akshare 的上游都是公网 API, 直连即可.
# 用户如需强制走代理, 设 AKSHARE_USE_ENV_PROXY=1.
if os.environ.get("AKSHARE_USE_ENV_PROXY") != "1":
    for _v in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY",
               "https_proxy", "http_proxy", "all_proxy"):
        os.environ.pop(_v, None)

_ak = None
def _lazy():
    global _ak
    if _ak is None:
        import akshare
        _ak = akshare
    return _ak


def _normalize_df_to_body(rows, fields=None):
    """list-of-dict → tushare body shape."""
    if not rows:
        return None
    # 过滤 fields
    if fields:
        fl = [f.strip() for f in fields.split(",")]
        rows = [{k: r.get(k) for k in fl} for r in rows]
    fields_out = list(rows[0].keys())
    items = [[r.get(k) for k in fields_out] for r in rows]
    return {
        "code": 0, "msg": "",
        "data": {
            "fields": fields_out,
            "items": items,
            "has_more": False,
            "count": len(items),
        },
    }


def _to_none(v):
    """pandas NaN → None, else unchanged."""
    try:
        import math
        if v is None: return None
        if isinstance(v, float) and math.isnan(v): return None
    except Exception:
        pass
    return v


def _hk_daily_tencent(ts_code, start, end, fields):
    """Tertiary fallback: tencent kline for HK daily (web.ifzq.gtimg.cn).

    Free, no quota, returns 20-day kline by default.
    Used when both tushare (10/day) and akshare/新浪 fail.
    """
    import urllib.request
    symbol = ts_code.split(".")[0]  # "00700.HK" → "00700"
    tc_code = f"hk{symbol}"

    # Tencent kline API: returns up to ~1000 bars
    url = f"http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={tc_code},day,,,320,qfq"
    try:
        req = urllib.request.Request(url)
        resp = urllib.request.urlopen(req, timeout=15)
        import json as _json
        data = _json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        sys.stderr.write(f"[tencent kline] fetch failed for {tc_code}: {e}\n")
        return None

    # Navigate response: data → data → hkXXXXX → day (or qfqday)
    inner = data.get("data", {}).get(tc_code, {})
    day_data = inner.get("qfqday") or inner.get("day")
    if not day_data:
        sys.stderr.write(f"[tencent kline] no day data for {tc_code}\n")
        return None

    def _fmt_date(d):
        # "2026-05-08" → "20260508"
        return d.replace("-", "") if d else ""

    start_cmp = start or ""
    end_cmp = end or ""

    rows = []
    for bar in day_data:
        # bar = ["2026-05-12", "462.000", "457.200", "469.000", "457.200", "32469707.000"]
        # fields: date, open, close, high, low, volume
        if len(bar) < 6:
            continue
        trade_date = _fmt_date(bar[0])
        if start_cmp and trade_date < start_cmp:
            continue
        if end_cmp and trade_date > end_cmp:
            continue
        rows.append({
            "ts_code":    ts_code,
            "trade_date": trade_date,
            "open":       _to_none(float(bar[1])) if bar[1] else None,
            "close":      _to_none(float(bar[2])) if bar[2] else None,
            "high":       _to_none(float(bar[3])) if bar[3] else None,
            "low":        _to_none(float(bar[4])) if bar[4] else None,
            "vol":        _to_none(float(bar[5])) if bar[5] else None,
            "amount":     None,   # tencent kline 不提供成交额
            "pct_chg":    None,
            "change":     None,
        })

    return _normalize_df_to_body(rows, fields) if rows else None


def _hk_daily(params, fields):
    """tushare hk_daily → akshare stock_hk_daily (新浪) → tencent kline (tertiary).

    Fallback chain:
      1. akshare stock_hk_daily (新浪数据源, 全部历史)
      2. tencent kline API (free, ~320 bars, no auth)

    注: 首选新浪 (stock_hk_daily) 而非东财 (stock_hk_hist) — 因为东财
    push2his.eastmoney.com 对我们 IP 存在 SSL MITM / 连接封锁 (实测
    RemoteDisconnected / SSL decryption failed). 新浪可用.
    新浪 API 不支持 start/end_date, 返回全部历史, 本地 filter.
    """
    ak = _lazy()
    ts_code = params.get("ts_code")
    if not ts_code: return None
    symbol = ts_code.split(".")[0]
    start = params.get("start_date") or ""
    end = params.get("end_date") or ""
    td = params.get("trade_date")
    if td and not start: start = td
    if td and not end:   end = td

    try:
        # stock_hk_daily 返回英文字段 (date/open/high/low/close/volume/amount)
        # 无日期 filter, 本地做
        df = ak.stock_hk_daily(symbol=symbol)
    except Exception as e:
        sys.stderr.write(f"[akshare] stock_hk_daily failed for {symbol}: {e}\n")
        # Tertiary fallback: tencent kline
        return _hk_daily_tencent(ts_code, start, end, fields)
    if df is None or df.empty:
        # Tertiary fallback: tencent kline
        return _hk_daily_tencent(ts_code, start, end, fields)

    # 本地 filter start/end (date 格式 YYYY-MM-DD)
    def _fmt(d):
        # "20260508" → "2026-05-08"
        if d and len(d) == 8 and d.isdigit():
            return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
        return d
    start_fmt = _fmt(start) if start else None
    end_fmt = _fmt(end) if end else None

    rows = []
    for _, r in df.iterrows():
        date_str = str(r.get("date", ""))
        if start_fmt and date_str < start_fmt: continue
        if end_fmt   and date_str > end_fmt:   continue
        row = {
            "ts_code":    ts_code,
            "trade_date": date_str.replace("-", ""),
            "open":       _to_none(r.get("open")),
            "close":      _to_none(r.get("close")),
            "high":       _to_none(r.get("high")),
            "low":        _to_none(r.get("low")),
            "vol":        _to_none(r.get("volume")),
            # 成交额: 新浪也是元 → tushare 千元 (÷1000)
            "amount":     (r.get("amount") / 1000.0) if r.get("amount") else None,
            # 新浪没提供 pct_chg 和 change, 留 None
            "pct_chg":    None,
            "change":     None,
        }
        rows.append(row)
    return _normalize_df_to_body(rows, fields) if rows else None


def _daily(params, fields):
    """tushare daily → akshare stock_zh_a_daily (新浪数据源, 支持日期范围).

    注: 用新浪而非东财 (stock_zh_a_hist) — 东财对我们 IP 挂了.
    需要转 ts_code 格式: "600519.SH" → "sh600519"
    """
    ak = _lazy()
    ts_code = params.get("ts_code")
    if not ts_code:
        # akshare 没有"一次拉全市场某日"的对应接口, 退回 None
        return None
    # ts_code "600519.SH" → "sh600519" (新浪要的 symbol)
    num, mkt = ts_code.split(".")
    symbol = f"{mkt.lower()}{num}"

    start = params.get("start_date") or ""
    end = params.get("end_date") or ""
    td = params.get("trade_date")
    if td and not start: start = td
    if td and not end:   end = td

    try:
        df = ak.stock_zh_a_daily(symbol=symbol, start_date=start, end_date=end, adjust="")
    except Exception as e:
        sys.stderr.write(f"[akshare] stock_zh_a_daily failed for {symbol}: {e}\n")
        return None
    if df is None or df.empty:
        return None

    rows = []
    for _, r in df.iterrows():
        row = {
            "ts_code":    ts_code,
            "trade_date": str(r.get("date", "")).replace("-", ""),
            "open":       _to_none(r.get("open")),
            "close":      _to_none(r.get("close")),
            "high":       _to_none(r.get("high")),
            "low":        _to_none(r.get("low")),
            "vol":        _to_none(r.get("volume")),
            "amount":     (r.get("amount") / 1000.0) if r.get("amount") else None,
            "pct_chg":    None,  # 新浪 API 无该字段, 交上层计算
            "change":     None,
        }
        rows.append(row)
    return _normalize_df_to_body(rows, fields) if rows else None


def _cctv_news(params, fields):
    """tushare cctv_news → akshare news_cctv."""
    ak = _lazy()
    date = params.get("date")
    if not date: return None

    try:
        df = ak.news_cctv(date=date)
    except Exception as e:
        sys.stderr.write(f"[akshare] news_cctv failed for {date}: {e}\n")
        return None
    if df is None or df.empty:
        return None

    rows = []
    for _, r in df.iterrows():
        row = {
            "date":    _to_none(r.get("date")),
            "title":   _to_none(r.get("title")),
            "content": _to_none(r.get("content")),
        }
        rows.append(row)
    return _normalize_df_to_body(rows, fields)


_HANDLERS = {
    "hk_daily":  _hk_daily,
    "daily":     _daily,
    "cctv_news": _cctv_news,
}


def available_for(api_name):
    return api_name in _HANDLERS


def fallback(api_name, params, fields):
    """尝试用 akshare 拿数据, 返回 tushare-shape body 或 None."""
    if api_name not in _HANDLERS:
        return None
    try:
        return _HANDLERS[api_name](params or {}, fields)
    except Exception as e:
        sys.stderr.write(f"[akshare fallback] {api_name} error: {e}\n")
        return None


if __name__ == "__main__":
    # CLI: `python3 akshare_fallback.py hk_daily ts_code=00700.HK start=20260505 end=20260508`
    import json as _json
    args = sys.argv[1:]
    if not args:
        sys.stderr.write("usage: akshare_fallback.py <api> key=val ...\n")
        sys.exit(2)
    api = args[0]
    params = {}
    for a in args[1:]:
        if "=" in a:
            k, v = a.split("=", 1)
            # shorthand
            if k == "start": k = "start_date"
            if k == "end": k = "end_date"
            params[k] = v
    body = fallback(api, params, fields=None)
    if body is None:
        print("(无数据)")
    else:
        print(_json.dumps(body, ensure_ascii=False, indent=2))
