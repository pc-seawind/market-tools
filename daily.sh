#!/usr/bin/env bash
# daily.sh — 每日市场趋势简报 (纯市场信号, 不依赖个人仓位/成本).
#
# 重要设计原则:
#   工具不控盘, 所以不发基于"你的成本价"的 P&L 告警 (那些没意义).
#   只发**市场层面的趋势信号** — 是否是买点/卖点, 基于技术指标 +
#   量价关系, 完全和用户的成本无关. 用户拿到市场判断后, 结合自己
#   的仓位决定操作.
#
# Usage:
#   daily.sh                      # 完整简报 (6 个维度)
#   daily.sh --holdings-only      # 只看关注清单的趋势信号
#   daily.sh --signals            # 只看触发的买卖点信号
#   daily.sh --themes             # 只看主题轮动
#
# 输出 6 段:
#   §1 关注清单趋势信号  — 每只股的市场层面买卖点判断
#   §2 信号总览         — 所有触发的信号汇总 (按类型分组)
#   §3 主题轮动         — concepts 板块今日 top/bottom
#   §4 美股隔夜         — NVDA / TSM / META / MU 等锚点
#   §5 政策信号         — 过去 3 天新闻联播关键词
#   §6 待观察扩展       — (暂用 §6 位置) 全市场技术形态异动股
#
# 市场信号规则 (纯技术指标, 无个人 cost):
#   买点:
#     📈 BUY_EARLY     量比 ≥ 2x + 1W ∈ [-3, +5] → 放量企稳, 早期吸筹
#     🎯 BUY_BREAKOUT  位置 ≥ 90 + 量比 ≥ 1.5x + 1W ∈ (0, 10) → 放量突破
#     💧 BUY_PULLBACK  1M ≥ +20 + 1W ∈ (-10, 0) + 量比 < 1 → 强势股健康回调
#   卖点:
#     ⚠️  SELL_EXHAUSTION 1W > +15 + 位置 > 85 → 末期加速, 情绪顶
#     📉 SELL_BREAKDOWN   1W < -10 + 量比 < 0.8 → 持续下跌 + 缩量破位
#     🔻 SELL_TOP         1M > +50 + 1W < 0 → 主升浪末端, 动能衰竭
#
# Env: TUSHARE_TOKEN required.

set -uo pipefail

here="$(dirname "$(readlink -f "$0")")"

exec python3 - "$here" "$@" <<'PY'
import csv, datetime, json, os, subprocess, sys
from collections import defaultdict, OrderedDict

here = sys.argv[1]
raw_args = sys.argv[2:]

mode = "full"
for arg in raw_args:
    if arg in ("-h", "--help"):
        with open(f"{here}/daily.sh") as f:
            lines = f.readlines()
        sys.stderr.write("".join(l[2:] if l.startswith("# ") else l[1:] if l.startswith("#") else ""
                                 for l in lines[1:40]))
        sys.exit(0)
    elif arg == "--holdings-only": mode = "holdings"
    elif arg == "--signals":       mode = "signals"
    elif arg == "--themes":        mode = "themes"
    else:
        sys.stderr.write(f"unknown arg: {arg}\n"); sys.exit(2)

sys.path.insert(0, here)
try:
    from watchlist_data import WATCHLIST, US_ANCHORS, all_codes, groups, has_hk
    import signals as sig_mod  # 信号检测模块 (也被 backtest.sh 共享)
except ImportError as e:
    sys.stderr.write(f"ERROR: 模块加载失败: {e}\n"); sys.exit(3)

def tushare(api, timeout=60, **params):
    args = ["python3", f"{here}/tushare.py", api]
    for k, v in params.items():
        if k == "fields": args.append(f"--fields={v}")
        else:             args.append(f"{k}={v}")
    args.append("--csv")
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return []
    if out.returncode != 0: return []
    return list(csv.DictReader(out.stdout.splitlines()))

def to_float(x, d=None):
    try: return float(x) if x not in (None, "", "None") else d
    except ValueError: return d

def ret_pct(cur, old):
    c1 = to_float(cur); c0 = to_float(old)
    if c1 is None or c0 is None or c0 == 0: return None
    return (c1 - c0) / c0 * 100

# ====================================================================
# 日期准备 + 全市场数据
# ====================================================================
today   = datetime.date.today().strftime("%Y%m%d")
# 需 60 交易日 lookback (SELL_TOP 需 r3m), 120 calendar days 稳妥覆盖
past120  = (datetime.date.today() - datetime.timedelta(days=120)).strftime("%Y%m%d")
cal = tushare("trade_cal", exchange="SSE", start_date=past120, end_date=today,
              fields="cal_date,is_open")
open_days = sorted([r["cal_date"] for r in cal if r.get("is_open") == "1"], reverse=True)
if not open_days:
    sys.stderr.write("ERROR: trade_cal 无数据\n"); sys.exit(4)

latest   = open_days[0]
prev_day = open_days[1] if len(open_days) > 1 else None
d_5d     = open_days[5] if len(open_days) > 5 else None
d_20d    = open_days[20] if len(open_days) > 20 else None
d_60d    = open_days[60] if len(open_days) > 60 else None

print(f"  [data] trade_date={latest} ...", file=sys.stderr)
db_latest = {r["ts_code"]: r for r in
             tushare("daily_basic", trade_date=latest,
                     fields="ts_code,close,turnover_rate,pe_ttm,pb,dv_ttm,total_mv")}
# T-1 fallback
if len(db_latest) < 100 and prev_day:
    latest = prev_day
    prev_day = open_days[2] if len(open_days) > 2 else None
    d_5d = open_days[6] if len(open_days) > 6 else None
    d_20d = open_days[21] if len(open_days) > 21 else None
    d_60d = open_days[61] if len(open_days) > 61 else None
    db_latest = {r["ts_code"]: r for r in
                 tushare("daily_basic", trade_date=latest,
                         fields="ts_code,close,turnover_rate,pe_ttm,pb,dv_ttm,total_mv")}

# A 股全市场 daily
daily_latest = {r["ts_code"]: r for r in
                tushare("daily", trade_date=latest, fields="ts_code,close,amount,pct_chg,high,low")}
daily_prev = {r["ts_code"]: r for r in
              tushare("daily", trade_date=prev_day, fields="ts_code,close")} if prev_day else {}
daily_5d = {r["ts_code"]: r for r in
            tushare("daily", trade_date=d_5d, fields="ts_code,close,amount")} if d_5d else {}
daily_20d = {r["ts_code"]: r for r in
             tushare("daily", trade_date=d_20d, fields="ts_code,close,amount")} if d_20d else {}
# 补拉 60d (SELL_TOP 需要 r3m)
daily_60d = {r["ts_code"]: r for r in
             tushare("daily", trade_date=d_60d, fields="ts_code,close")} if d_60d else {}

# A 股个股 fallback (2026-06-11)
# 全市场 daily/daily_basic 截面只走 tushare, 无兜底。一旦 tushare 超时/限速,
# 整个截面为空 → 关注清单全表 "[无数据]"。这里给 watchlist 里的 A 股个股
# 补一层 quote_sources.daily_bars (mootdx primary → tencent fallback),
# 与港股同模式。只对 *截面里缺失* 的个股触发 (tushare 正常时零代价)。
# 腾讯/mootdx kline 无 amount(成交额), 用 close×volume(手)×0.1 ≈ 千元 近似,
# 让量比信号在降级时仍可用 (港股是直接留空, A 股这里更进一步)。
def _ashare_rows_from_quote(code):
    try:
        from quote_sources import daily_bars
        bars = daily_bars(code, days=70)  # 需 60 交易日 lookback
    except Exception as e:
        sys.stderr.write(f"    [WARN] {code} daily_bars fallback error: {e}\n")
        return []
    out = []
    prev_close = None
    # daily_bars 返回升序, daily.sh 下游期望降序 (rows[0]=latest)
    for b in bars:
        close = to_float(b.get("close")) or 0
        vol = to_float(b.get("volume")) or 0  # 手
        pct = ((close / prev_close - 1) * 100) if prev_close else None
        out.append({
            "ts_code": code,
            "trade_date": str(b.get("date", "")).replace("-", ""),
            "close": close,
            "high": to_float(b.get("high")) or 0,
            "low": to_float(b.get("low")) or 0,
            "amount": round(close * vol * 0.1, 3) if (close and vol) else "",  # ≈ 千元
            "pct_chg": round(pct, 2) if pct is not None else "",
        })
        if close:
            prev_close = close
    out.sort(key=lambda r: r.get("trade_date", ""), reverse=True)
    return out

a_tickers = [c for c, _n, _t in all_codes() if not c.endswith(".HK")]
a_missing = [c for c in a_tickers if c not in daily_latest]
if a_missing:
    sys.stderr.write(
        f"  [data] A股截面缺 {len(a_missing)}/{len(a_tickers)} 只 (tushare 降级?), "
        f"逐个 daily_bars 兜底 ...\n")
    a_recovered = 0
    for code in a_missing:
        rows = _ashare_rows_from_quote(code)
        if not rows:
            sys.stderr.write(f"    [WARN] {code} 无数据 (tushare + daily_bars 都失败)\n")
            continue
        daily_latest[code] = rows[0]
        if len(rows) > 5:  daily_5d[code]  = rows[5]
        if len(rows) > 20: daily_20d[code] = rows[20]
        if len(rows) > 60: daily_60d[code] = rows[60]
        # daily_basic 缺失 (pe/pb/turnover) → 用 daily_latest 的 close 补最小集,
        # 让 compute_metrics 的 cur 有值; 估值类字段降级为 None (信号不依赖)。
        if code not in db_latest:
            db_latest[code] = {"ts_code": code, "close": rows[0].get("close")}
        a_recovered += 1
    sys.stderr.write(f"    A股兜底: {a_recovered}/{len(a_missing)} 恢复\n")

# 港股数据 (如果 watchlist 里有 HK 标的)
# 重要: 港股默认走 quote_sources 的腾讯财经日K。tushare hk_daily 限速 10 次/天，且在
# cron 中可能慢/卡/超配额；如果先走 tushare，fallback 往往来不及执行，表现为"港股取不到"。
# 因此路由改为: Tencent primary → tushare hk_daily secondary。
# 腾讯源字段不含 amount/pct_chg，pct_chg 在这里用 close 计算补上。
if has_hk():
    hk_tickers = [c for c, _n, _t in all_codes() if c.endswith(".HK")]
    print(f"  [data] fetching HK per-ticker (N={len(hk_tickers)}) tencent→hk_daily fallback ...",
          file=sys.stderr)
    fail_cnt = 0
    fallback_cnt = 0

    def _hk_rows_from_tencent(code):
        try:
            from quote_sources import daily_bars
            bars = daily_bars(code, days=120)
        except Exception as e:
            sys.stderr.write(f"    [WARN] {code} tencent fallback error: {e}\n")
            return []
        out = []
        prev_close = None
        for b in bars:
            close = float(b.get("close") or 0)
            pct = ((close / prev_close - 1) * 100) if prev_close else None
            trade_date = str(b.get("date", "")).replace("-", "")
            out.append({
                "ts_code": code,
                "trade_date": trade_date,
                "close": close,
                "high": float(b.get("high") or 0),
                "low": float(b.get("low") or 0),
                "amount": "",  # 腾讯 kline 未提供成交额；下游需容忍空值
                "pct_chg": round(pct, 2) if pct is not None else "",
            })
            if close:
                prev_close = close
        return out

    for code in hk_tickers:
        # 港股 primary: 腾讯财经。避免 tushare hk_daily 配额/慢请求阻塞 cron。
        rows = _hk_rows_from_tencent(code)
        source = "tencent"
        if rows:
            fallback_cnt += 1
        else:
            rows = tushare("hk_daily", ts_code=code,
                           start_date=past120, end_date=today,
                           fields="trade_date,close,amount,pct_chg,high,low")
            source = "hk_daily"
        if not rows:
            fail_cnt += 1
            sys.stderr.write(f"    [WARN] {code} 无数据 (tencent + hk_daily fallback 都失败)\n")
            continue
        # 归一化 + 映射
        rows.sort(key=lambda r: r.get("trade_date", ""), reverse=True)
        for r in rows: r["ts_code"] = code
        daily_latest[code] = rows[0]
        if len(rows) > 5:  daily_5d[code]  = rows[5]
        if len(rows) > 20: daily_20d[code] = rows[20]
        if len(rows) > 60: daily_60d[code] = rows[60]
        if source == "tencent":
            sys.stderr.write(f"    [fallback] {code} HK bars from tencent ({len(rows)} rows)\n")
    print(f"    HK: {len(hk_tickers) - fail_cnt}/{len(hk_tickers)} OK "
          f"({fail_cnt} 失败, {fallback_cnt} tencent primary)", file=sys.stderr)

# ====================================================================
# Header
# ====================================================================
import datetime as _dt
now = _dt.datetime.now()
print()
print("╔════════════════════════════════════════════════════════════════════════╗")
print(f"║  📅 MARKET TREND BRIEF  ·  {now.strftime('%Y-%m-%d %H:%M')}  ·  数据日: {latest}".ljust(73) + "║")
print("╚════════════════════════════════════════════════════════════════════════╝")

# ====================================================================
# 市场信号检测 (核心逻辑, 纯市场数据)
# ====================================================================
def compute_metrics(code):
    """为一只股票计算所有需要的 metric, 返回 dict."""
    dl = daily_latest.get(code, {})
    db = db_latest.get(code, {})
    d5 = daily_5d.get(code, {})
    d20 = daily_20d.get(code, {})
    d60 = daily_60d.get(code, {})

    cur = to_float(dl.get("close")) or to_float(db.get("close"))
    if cur is None: return None

    amt_cur = (to_float(dl.get("amount")) or 0) / 1e5   # 千元 → 亿
    amt_20d = (to_float(d20.get("amount")) or 0) / 1e5

    r1w = ret_pct(cur, d5.get("close"))
    r1m = ret_pct(cur, d20.get("close"))
    r3m = ret_pct(cur, d60.get("close"))
    vol_ratio = amt_cur / amt_20d if amt_20d > 0 else None

    # 位置近似 (由 signals 模块统一计算)
    pos = sig_mod.position_proxy(r1m)

    return {
        "close": cur,
        "pct_chg": to_float(dl.get("pct_chg")),
        "high": to_float(dl.get("high")),
        "low": to_float(dl.get("low")),
        "pe_ttm": to_float(db.get("pe_ttm")),
        "pb": to_float(db.get("pb")),
        "turnover_rate": to_float(db.get("turnover_rate")),
        "amt_cur": amt_cur,
        "amt_20d": amt_20d,
        "vol_ratio": vol_ratio,
        "r1w": r1w,
        "r1m": r1m,
        "r3m": r3m,
        "pos": pos,
    }

def market_signals(m):
    """从 metrics dict 推信号. 委托给 signals.py 模块 (规则统一维护).

    Returns: list of (icon, signal_type, description)
    """
    if not m: return []
    # 映射 daily.sh 的 metrics 字段名到 signals.py 期望的格式
    sig_input = {
        "r1w":           m.get("r1w"),
        "r1m":           m.get("r1m"),
        "r3m":           m.get("r3m"),
        "vol_ratio":     m.get("vol_ratio"),
        "pos":           m.get("pos"),
        "pct_chg_today": m.get("pct_chg"),
    }
    return sig_mod.detect(sig_input)


# ====================================================================
# §1 关注清单趋势信号
# ====================================================================
if mode in ("full", "holdings", "signals"):
    print(f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"  §1. 关注清单  ·  市场趋势信号  (基于技术 + 量价, 与个人仓位无关)")
    print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    all_signals = []  # 汇总所有信号
    grps = groups()

    def fmt_pct(v, w=7):
        if v is None: return "n/a".rjust(w)
        return f"{v:+{w-1}.2f}%"

    for tier, stocks in grps.items():
        print(f"\n  ▣ {tier}")
        print(f"    {'代码':<12}{'名称':<10}{'现价':>9}{'今日':>9}{'1W':>9}{'1M':>9}"
              f"{'量比':>7}{'位置':>7}  │  信号")
        for code, name in stocks:
            m = compute_metrics(code)
            if not m:
                print(f"    {code:<12}{name[:8]:<10}  [无数据 (停牌?)]")
                continue
            sigs = market_signals(m)
            sig_str = "  ".join(icon for icon, _t, _d in sigs) if sigs else "  "
            for s in sigs: all_signals.append((tier, code, name, s))

            vr_str = f"{m['vol_ratio']:.1f}x" if m['vol_ratio'] else "n/a"
            pos_str = f"{m['pos']:.0f}%" if m['pos'] is not None else "n/a"
            print(f"    {code:<12}{name[:8]:<10} ¥{m['close']:>7.2f} "
                  f"{fmt_pct(m['pct_chg']):>8} {fmt_pct(m['r1w']):>8} "
                  f"{fmt_pct(m['r1m']):>8} {vr_str:>6} {pos_str:>6}  │  {sig_str}")

    # §2 信号汇总
    if all_signals:
        print(f"\n  🔔 触发信号汇总 ({len(all_signals)} 条):")
        # 按类型分组
        by_type = defaultdict(list)
        for tier, code, name, sig in all_signals:
            by_type[sig[1]].append((tier, code, name, sig))

        # 排序顺序: 买点先, 卖点后
        type_order = ["BUY_EARLY", "BUY_BREAKOUT",
                      "SELL_EXHAUSTION", "SELL_CONFIRMED", "SELL_EXTREME",
                      "SELL_TOP",
                      "TODAY_SURGE", "TODAY_DROP"]
        for sig_type in type_order:
            if sig_type not in by_type: continue
            items = by_type[sig_type]
            print()
            icon_sample = items[0][3][0]
            print(f"    {icon_sample}  {sig_type}  ({len(items)} 只)")
            for tier, code, name, sig in items:
                print(f"        [{tier}] {code} {name}")
                print(f"            {sig[2]}")
    else:
        print(f"\n  ✅ 所有关注股票当前无明确买/卖点信号, 量价平稳")

# ====================================================================
# signals mode 到此结束
# ====================================================================
if mode == "signals":
    sys.exit(0)

# ====================================================================
# §3 主题轮动
# ====================================================================
if mode in ("full", "themes"):
    print(f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"  §3. 主题轮动  (今日 vs 5 日前)")
    print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    try:
        from concepts_data import CONCEPTS
    except ImportError:
        CONCEPTS = {}

    concept_stats = []
    for concept, stocks in CONCEPTS.items():
        r_today, r_1w, amts = [], [], []
        for code, _n in stocks:
            dl = daily_latest.get(code, {})
            d5 = daily_5d.get(code, {})
            v = to_float(dl.get("pct_chg"))
            if v is not None: r_today.append(v)
            r1w = ret_pct(dl.get("close"), d5.get("close"))
            if r1w is not None: r_1w.append(r1w)
            amts.append((to_float(dl.get("amount")) or 0) / 1e5)
        concept_stats.append({
            "name": concept,
            "avg_today": sum(r_today)/len(r_today) if r_today else None,
            "avg_1w":    sum(r_1w)/len(r_1w) if r_1w else None,
            "total_amt": sum(amts),
        })

    concept_stats.sort(key=lambda s: -(s["avg_today"] or -1e9))

    print(f"\n  {'排名':<4}{'概念':<28}{'今日均涨':>10}{'5日均涨':>10}{'日成交(亿)':>14}")
    print(f"  {'-' * 65}")
    def mark(v):
        if v is None: return "  "
        if v > 2: return "🔥"
        if v < -2: return "🧊"
        return "  "
    for i, cs in enumerate(concept_stats[:5], 1):
        today_str = f"{cs['avg_today']:+.2f}%" if cs["avg_today"] is not None else "n/a"
        w1_str = f"{cs['avg_1w']:+.1f}%" if cs["avg_1w"] is not None else "n/a"
        print(f"  {mark(cs['avg_today'])}{i:>2}. {cs['name']:<26} "
              f"{today_str:>9} {w1_str:>9} {cs['total_amt']:>11.0f}")
    print(f"  {'-' * 65}")
    for i, cs in enumerate(concept_stats[-3:], len(concept_stats) - 2):
        today_str = f"{cs['avg_today']:+.2f}%" if cs["avg_today"] is not None else "n/a"
        w1_str = f"{cs['avg_1w']:+.1f}%" if cs["avg_1w"] is not None else "n/a"
        print(f"  {mark(cs['avg_today'])}{i:>2}. {cs['name']:<26} "
              f"{today_str:>9} {w1_str:>9} {cs['total_amt']:>11.0f}")

if mode == "themes":
    sys.exit(0)

# ====================================================================
# §4 美股隔夜
# ====================================================================
if mode == "full":
    print(f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"  §4. 美股隔夜 (AI 链跨市场锚点)")
    print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"  {'ticker':<8}{'名称':<15}{'价格':>9}{'开盘':>9}{'区间':>22}")
    print(f"  {'-' * 60}")
    for ticker, name in US_ANCHORS:
        try:
            res = subprocess.run(["bash", f"{here}/quote.sh", ticker],
                                 capture_output=True, text=True, timeout=10)
            if res.returncode == 0 and "|" in res.stdout:
                first_line = res.stdout.strip().split("\n")[0]
                fields = dict(f.split("=", 1) for f in first_line.split(" | ")[1].split(" ")
                              if "=" in f)
                price = fields.get("price", "?")
                open_p = fields.get("open", "?")
                hi = fields.get("high", "?")
                lo = fields.get("low", "?")
                # 计算隔夜涨跌 (price vs open)
                try:
                    pct = (float(price) - float(open_p)) / float(open_p) * 100
                    tag = "🔥" if pct > 2 else ("🧊" if pct < -2 else "  ")
                    pct_str = f"({pct:+.1f}%)"
                except Exception:
                    tag, pct_str = "  ", ""
                print(f"  {tag}{ticker:<6}{name:<15}${price:>8}  ${open_p:>7}  "
                      f"[${lo} — ${hi}] {pct_str}")
            else:
                print(f"  {ticker:<8}{name:<15}  [获取失败]")
        except Exception as e:
            print(f"  {ticker:<8}{name:<15}  [错误: {e}]")

# ====================================================================
# §5 政策信号
# ====================================================================
if mode == "full":
    print(f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"  §5. 政策信号 (过去 3 日 CCTV 联播 关键词)")
    print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    try:
        res = subprocess.run(
            ["bash", f"{here}/policy.sh", "days=3",
             "--grep=半导体|芯片|AI|算力|基础研究|科技|制造业|新能源|金融|消费"],
            capture_output=True, text=True, timeout=200,
        )
        if res.returncode == 0:
            for line in res.stdout.split("\n"):
                if line.startswith("[20") or line.startswith("  ·"):
                    print(f"  {line}")
        else:
            print(f"  [policy.sh 获取失败]")
    except subprocess.TimeoutExpired:
        print(f"  [policy.sh 超时]")
    except Exception as e:
        print(f"  [错误: {e}]")

print()
PY
