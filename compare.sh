#!/usr/bin/env bash
# compare.sh — side-by-side 多股横向对比 (2-N ticker).
#
# 投研 flow 里最常见的动作: "A 和 B 哪个更值得买" (茅台 vs 五粮液,
# 寒武纪 vs 海光, 中际旭创 vs 新易盛). 之前只能跑两次 diligence + 人
# 脑拼接对比; compare.sh 直接输出并列对比表, 每个维度一张.
#
# Usage:
#   compare.sh <ticker1> <ticker2> [ticker3 ...]    # 2-5 只
#
# 覆盖维度 (仅 A 股; HK/US 降级到能拉的那些):
#   §1 估值对比       — 收盘/PE_TTM/PB/PS/股息率/市值
#   §2 业绩对比       — 最新季度 ROE/ROA/营收YoY/净利YoY/毛利/净利/负债率
#   §3 量价对比       — 120天位置/最大回撤/年化波动率/1W 1M 3M 收益
#   §4 机构动作对比   — 北向 QoQ / 公募 QoQ / 产业资本 QoQ (4 季度累计)
#
# 设计:
#   - 每只 ticker 单独跑 5 API calls (快照 + 日线 + daily_basic +
#     fina_indicator + top10_floatholders), 共 5N calls (2 只 ~10s,
#     5 只 ~30s, 受 tushare rate-limit 制约)
#   - 数据收集用 Python dict {ticker: metrics}, 最后 pivot 成对比表
#   - 缺失值显示 n/a, 不中断流程
#
# Example:
#   compare.sh sh688256 sh688041 sh688981       # 寒武纪 / 海光 / 中芯
#   compare.sh sz300308 sz300502                # 中际旭创 vs 新易盛
#   compare.sh sh600519 sz000858 sz000568       # 白酒三巨头
#
# Env: TUSHARE_TOKEN required.

set -uo pipefail

# Arg check
if [[ $# -lt 2 || "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    cat >&2 <<'EOF'
usage: compare.sh <ticker1> <ticker2> [ticker3 ...]

每只 ticker 用 sh/sz/bj prefix (仅 A 股, HK/US 降级处理).
最多 5 只并排对比 (更多会影响表格可读性).

examples:
  compare.sh sh688256 sh688041 sh688981    # 3 股对比
  compare.sh sz300308 sz300502             # 2 股对比
EOF
    exit 2
fi

if [[ $# -gt 5 ]]; then
    echo "ERROR: 最多 5 只股票对比 (avoid 表格过宽)" >&2
    exit 2
fi

here="$(dirname "$(readlink -f "$0")")"

exec python3 - "$here" "$@" <<'PY'
import csv, datetime, math, statistics, subprocess, sys

here = sys.argv[1]
tickers = sys.argv[2:]

def ts_code_of(ticker):
    t = ticker.lower()
    if   t[:2] == "sh" and t[2:].isdigit(): return f"{t[2:]}.SH"
    elif t[:2] == "sz" and t[2:].isdigit(): return f"{t[2:]}.SZ"
    elif t[:2] == "bj" and t[2:].isdigit(): return f"{t[2:]}.BJ"
    else:
        sys.stderr.write(f"ERROR: {ticker!r} 不是 A 股格式 (需 sh/sz/bj prefix)\n")
        sys.exit(2)

ts_codes = {t: ts_code_of(t) for t in tickers}

def tushare(api, timeout=60, **params):
    args = ["python3", f"{here}/tushare.py", api]
    for k, v in params.items():
        if k == "fields": args.append(f"--fields={v}")
        else:             args.append(f"{k}={v}")
    args.append("--csv")
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        sys.stderr.write(f"[WARN] tushare {api} timeout\n")
        return []
    if out.returncode != 0:
        sys.stderr.write(f"[WARN] tushare {api} failed: {out.stderr.strip()[-150:]}\n")
        return []
    return list(csv.DictReader(out.stdout.splitlines()))

def to_float(x, d=None):
    try: return float(x) if x not in (None, "", "None") else d
    except ValueError: return d

# --- collect metrics per ticker ---
import datetime as _dt
today    = _dt.date.today().strftime("%Y%m%d")
back_7d  = (_dt.date.today() - _dt.timedelta(days=7)).strftime("%Y%m%d")
back_180 = (_dt.date.today() - _dt.timedelta(days=180)).strftime("%Y%m%d")

metrics = {}  # metrics[ticker] = dict of fields

for ticker, ts_code in ts_codes.items():
    print(f"  pulling {ticker} ({ts_code})...", file=sys.stderr)
    m = {"ts_code": ts_code, "ticker": ticker, "name": ticker}

    # 1. daily_basic (最新估值)
    db = tushare("daily_basic", ts_code=ts_code, start_date=back_7d, end_date=today,
                 fields="ts_code,trade_date,close,turnover_rate,pe_ttm,pb,ps_ttm,dv_ttm,total_mv,circ_mv")
    if db:
        latest = db[0]
        m.update({
            "trade_date": latest.get("trade_date"),
            "close": to_float(latest.get("close")),
            "turnover_rate": to_float(latest.get("turnover_rate")),
            "pe_ttm": to_float(latest.get("pe_ttm")),
            "pb":     to_float(latest.get("pb")),
            "ps_ttm": to_float(latest.get("ps_ttm")),
            "dv_ttm": to_float(latest.get("dv_ttm")),
            "total_mv_yi": (to_float(latest.get("total_mv")) or 0) / 1e4,  # 万元→亿元
        })

    # 2. stock_basic (name/industry)
    sb = tushare("stock_basic", ts_code=ts_code, fields="ts_code,name,industry")
    if sb:
        m["name"] = sb[0].get("name", ticker)
        m["industry"] = sb[0].get("industry", "?")

    # 3. fina_indicator (最近一期)
    fi = tushare("fina_indicator", ts_code=ts_code,
                 fields="ts_code,end_date,roe,roa,netprofit_yoy,or_yoy,grossprofit_margin,netprofit_margin,debt_to_assets")
    if fi:
        latest_fi = fi[0]
        m.update({
            "fi_end_date":   latest_fi.get("end_date"),
            "roe":           to_float(latest_fi.get("roe")),
            "roa":           to_float(latest_fi.get("roa")),
            "or_yoy":        to_float(latest_fi.get("or_yoy")),
            "netprofit_yoy": to_float(latest_fi.get("netprofit_yoy")),
            "gross_margin":  to_float(latest_fi.get("grossprofit_margin")),
            "net_margin":    to_float(latest_fi.get("netprofit_margin")),
            "debt_ratio":    to_float(latest_fi.get("debt_to_assets")),
        })

    # 4. daily (120 天历史, 技术指标)
    hist = tushare("daily", ts_code=ts_code, start_date=back_180, end_date=today,
                   fields="trade_date,close,high,low")
    if len(hist) >= 20:
        closes = [to_float(r["close"]) for r in hist if to_float(r["close"]) is not None]
        highs  = [to_float(r["high"])  for r in hist if to_float(r["high"]) is not None]
        lows   = [to_float(r["low"])   for r in hist if to_float(r["low"])  is not None]
        closes = closes[:120]
        chrono = list(reversed(closes))
        peak = chrono[0]; mdd = 0
        for p in chrono:
            if p > peak: peak = p
            dd = (p - peak) / peak * 100
            if dd < mdd: mdd = dd
        rets = [math.log(chrono[i+1]/chrono[i]) for i in range(len(chrono)-1)]
        ann_vol = statistics.pstdev(rets) * math.sqrt(252) * 100 if rets else None
        hi120 = max(highs[:120]) if highs else None
        lo120 = min(lows[:120])  if lows  else None
        cur = closes[0] if closes else None
        pos120 = None
        if hi120 and lo120 and cur and hi120 > lo120:
            pos120 = (cur - lo120) / (hi120 - lo120) * 100
        def ret_n(n):
            if n >= len(closes): return None
            c0 = closes[n]
            return (cur - c0) / c0 * 100 if c0 else None
        m.update({
            "hi120": hi120, "lo120": lo120, "pos120": pos120,
            "mdd120": mdd, "ann_vol": ann_vol,
            "r1w":  ret_n(5),
            "r1m":  ret_n(20),
            "r3m":  ret_n(60),
        })

    # 5. top10_floatholders (机构动作, 4 quarter QoQ)
    th = tushare("top10_floatholders", ts_code=ts_code,
                 fields="ts_code,end_date,holder_name,hold_ratio")
    if th:
        # group by quarter
        from collections import defaultdict
        by_q = defaultdict(list)
        for h in th:
            q = h.get("end_date")
            if q: by_q[q].append(h)
        qs = sorted(by_q.keys(), reverse=True)[:5]  # 最多 5 期用于 QoQ
        # classify each row
        def classify(name):
            name = (name or "").replace(" ", "")
            if "香港中央结算" in name: return "北向"
            if any(k in name for k in ["证券投资基金","ETF","交易型开放式指数"]):
                return "公募"
            if "保险" in name: return "险资"
            if any(k in name for k in ["MORGANSTANLEY","GOLDMANSACHS","UBS","JPMORGAN","Lumiza"]):
                return "外资"
            if any(k in name for k in ["投资控股","集团","控股公司","有限公司"]):
                return "产业资本"
            return "其他"
        # aggregate per-quarter per-cat
        cat_by_q = {}
        for q in qs:
            total = defaultdict(float)
            for r in by_q[q]:
                total[classify(r.get("holder_name"))] += (to_float(r.get("hold_ratio")) or 0)
            cat_by_q[q] = total
        # QoQ 变化 (最新 vs 最早)
        if len(qs) >= 2:
            q_new, q_old = qs[0], qs[-1]
            # Compact format: YYQn→YYQn  (e.g. 25Q1→26Q1)
            def short_q(d):
                y = d[2:4]
                mo = int(d[4:6])
                q = (mo - 1) // 3 + 1
                return f"{y}Q{q}"
            m["qoq_quarters"] = f"{short_q(q_old)}→{short_q(q_new)}"
            for cat in ("北向","公募","外资","产业资本"):
                m[f"qoq_{cat}"] = cat_by_q[q_new].get(cat, 0) - cat_by_q[q_old].get(cat, 0)
            m["latest_beixiang"] = cat_by_q[q_new].get("北向", 0)
            m["latest_gongmu"]   = cat_by_q[q_new].get("公募", 0)

    metrics[ticker] = m

# --- render comparison tables ---
def fmt_val(v, w=10, p=2, suffix=""):
    if v is None: return "n/a".rjust(w)
    return f"{v:{w-len(suffix)}.{p}f}{suffix}"

def fmt_pct(v, w=10):
    if v is None: return "n/a".rjust(w)
    return f"{v:+{w-1}.2f}%"

def fmt_str(v, w=10):
    if v is None: return "n/a".rjust(w)
    return str(v)[:w].rjust(w)

col_w = 14
names = [metrics[t].get("name", t) for t in tickers]

def print_table(title, rows):
    """rows = list of (label, [value-per-ticker, ...], formatter)"""
    print()
    print("=" * (16 + col_w * len(tickers)))
    print(f" {title}")
    print("=" * (16 + col_w * len(tickers)))
    # Header
    hdr = "  " + "指标".ljust(14)
    for n in names: hdr += str(n)[:col_w-1].rjust(col_w)
    print(hdr)
    print("  " + "-" * (14 + col_w * len(tickers)))
    for label, vals, fmter in rows:
        row = "  " + label.ljust(14)
        for v in vals: row += fmter(v).rjust(col_w)
        print(row)

print()
print("╔" + "═" * (14 + col_w * len(tickers) + 2) + "╗")
print(f"║  📊 MULTI-STOCK COMPARE  ·  {len(tickers)} tickers".ljust(14 + col_w * len(tickers) + 3) + "║")
print(f"║  生成: {datetime.datetime.now():%Y-%m-%d %H:%M}".ljust(14 + col_w * len(tickers) + 3) + "║")
print("╚" + "═" * (14 + col_w * len(tickers) + 2) + "╝")

# Ticker header with code + name + industry
print()
print(f"  {'ts_code':<14}" + "".join(metrics[t].get("ts_code", "")[:col_w-1].rjust(col_w) for t in tickers))
print(f"  {'行业':<14}" + "".join(str(metrics[t].get("industry","?"))[:col_w-1].rjust(col_w) for t in tickers))
print(f"  {'报告期':<14}" + "".join(str(metrics[t].get("fi_end_date","?"))[:col_w-1].rjust(col_w) for t in tickers))

# §1 估值对比
print_table("§1. 估值对比", [
    ("收盘价",        [metrics[t].get("close") for t in tickers], lambda v: fmt_val(v, 10, 2)),
    ("PE_TTM",       [metrics[t].get("pe_ttm") for t in tickers], lambda v: fmt_val(v, 10, 2)),
    ("PB",           [metrics[t].get("pb") for t in tickers], lambda v: fmt_val(v, 10, 2)),
    ("PS_TTM",       [metrics[t].get("ps_ttm") for t in tickers], lambda v: fmt_val(v, 10, 2)),
    ("股息率%",       [metrics[t].get("dv_ttm") for t in tickers], lambda v: fmt_val(v, 10, 2)),
    ("换手率%",       [metrics[t].get("turnover_rate") for t in tickers], lambda v: fmt_val(v, 10, 2)),
    ("总市值(亿)",   [metrics[t].get("total_mv_yi") for t in tickers], lambda v: fmt_val(v, 10, 1)),
])

# §2 业绩对比 (最新一期)
print_table("§2. 业绩对比 (最新财报)", [
    ("ROE %",        [metrics[t].get("roe") for t in tickers], fmt_pct),
    ("ROA %",        [metrics[t].get("roa") for t in tickers], fmt_pct),
    ("营收 YoY %",   [metrics[t].get("or_yoy") for t in tickers], fmt_pct),
    ("净利 YoY %",   [metrics[t].get("netprofit_yoy") for t in tickers], fmt_pct),
    ("毛利率 %",     [metrics[t].get("gross_margin") for t in tickers], fmt_pct),
    ("净利率 %",     [metrics[t].get("net_margin") for t in tickers], fmt_pct),
    ("资产负债率 %", [metrics[t].get("debt_ratio") for t in tickers], fmt_pct),
])

# §3 量价对比 (120 天)
print_table("§3. 量价对比 (近 120 天)", [
    ("120 天区间高", [metrics[t].get("hi120") for t in tickers], lambda v: fmt_val(v, 10, 2)),
    ("120 天区间低", [metrics[t].get("lo120") for t in tickers], lambda v: fmt_val(v, 10, 2)),
    ("120 天位置 %", [metrics[t].get("pos120") for t in tickers], lambda v: fmt_val(v, 10, 1)),
    ("最大回撤 %",   [metrics[t].get("mdd120") for t in tickers], fmt_pct),
    ("年化波动 %",   [metrics[t].get("ann_vol") for t in tickers], lambda v: fmt_val(v, 10, 1)),
    ("1W 收益 %",    [metrics[t].get("r1w") for t in tickers], fmt_pct),
    ("1M 收益 %",    [metrics[t].get("r1m") for t in tickers], fmt_pct),
    ("3M 收益 %",    [metrics[t].get("r3m") for t in tickers], fmt_pct),
])

# §4 机构动作 (4 quarter QoQ)
print_table("§4. 机构动作 QoQ (4 季度累计变化 pp)", [
    ("QoQ 期间",      [metrics[t].get("qoq_quarters") for t in tickers], lambda v: fmt_str(v, 10)),
    ("北向 %",       [metrics[t].get("latest_beixiang") for t in tickers], lambda v: fmt_val(v, 10, 2)),
    ("北向 Δpp",     [metrics[t].get("qoq_北向") for t in tickers], fmt_pct),
    ("公募 %",       [metrics[t].get("latest_gongmu") for t in tickers], lambda v: fmt_val(v, 10, 2)),
    ("公募 Δpp",     [metrics[t].get("qoq_公募") for t in tickers], fmt_pct),
    ("外资 Δpp",     [metrics[t].get("qoq_外资") for t in tickers], fmt_pct),
    ("产业资本 Δpp", [metrics[t].get("qoq_产业资本") for t in tickers], fmt_pct),
])

# Footer
print()
print("  💡 下一步: 对优胜标的跑 diligence.sh 做完整六维分析")
best_by_r1m = max(tickers, key=lambda t: metrics[t].get("r1m", -1e9) or -1e9)
print(f"  示例:     bash {here}/diligence.sh {best_by_r1m}")
print()
PY
