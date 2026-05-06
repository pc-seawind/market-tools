#!/usr/bin/env bash
# daily.sh — 每日监控简报 (持仓健康 + 主题轮动 + 美股隔夜 + 政策信号 + 新候选).
#
# 解决的问题:
#   选股工具 (funnel / momentum) 只是入口, 真正的 alpha 来自持续跟踪.
#   daily.sh 扫描 watchlist_data.py 里的持仓 + 观察清单, 在一份简报里
#   呈现 6 个维度的日度变化, 识别需要行动的信号.
#
# Usage:
#   daily.sh                      # 完整简报 (所有维度)
#   daily.sh --holdings-only      # 只看持仓动态 + 信号触发
#   daily.sh --alerts             # 只看触发警报的股
#   daily.sh --themes             # 只看主题轮动
#
# 输出 6 段:
#   §1 持仓健康度     — 每只股今日 ±% / P&L / 信号触发
#   §2 组合总览       — 各仓位 P&L / 最大风险股
#   §3 主题轮动       — concepts 板块今日热度
#   §4 美股隔夜       — NVDA / TSM / META / AAPL 等
#   §5 政策信号       — 过去 3 天新闻联播关键词
#   §6 观察清单动态   — 观察中的股今日表现 + funnel/momentum 有没有上新榜
#
# 信号触发规则 (对每只持仓):
#   🚨 STOP_LOSS    - 价格 < 成本 × 0.90 (博弈仓) 或 0.85 (基础仓)
#   ✅ TAKE_PROFIT  - 价格 > 成本 × 1.30 (博弈仓) 或 1.50 (基础仓)
#   ⚠️  REDUCE      - 1W 涨幅 > +15% (末期情绪顶, 分批减仓)
#   💡 ADD          - 基础仓跌到成本 -8% 但基本面未变 (加仓机会)
#
# Env: TUSHARE_TOKEN required.
# Deps: bash + python3 stdlib + watchlist_data.py + concepts_data.py

set -uo pipefail

here="$(dirname "$(readlink -f "$0")")"

exec python3 - "$here" "$@" <<'PY'
import csv, datetime, subprocess, sys
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
    elif arg == "--alerts":        mode = "alerts"
    elif arg == "--themes":        mode = "themes"
    else:
        sys.stderr.write(f"unknown arg: {arg}\n"); sys.exit(2)

sys.path.insert(0, here)
try:
    from watchlist_data import HOLDINGS, WATCHLIST, US_ANCHORS, all_holdings, total_weight
except ImportError as e:
    sys.stderr.write(f"ERROR: watchlist_data.py 加载失败: {e}\n"); sys.exit(3)

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
# 拉日期 + 全市场数据 (for 持仓 + 观察 + 主题)
# ====================================================================
today   = datetime.date.today().strftime("%Y%m%d")
past30  = (datetime.date.today() - datetime.timedelta(days=30)).strftime("%Y%m%d")
cal = tushare("trade_cal", exchange="SSE", start_date=past30, end_date=today,
              fields="cal_date,is_open")
open_days = sorted([r["cal_date"] for r in cal if r.get("is_open") == "1"], reverse=True)
if not open_days:
    sys.stderr.write("ERROR: trade_cal 无数据\n"); sys.exit(4)

latest = open_days[0]
prev_day = open_days[1] if len(open_days) > 1 else None
d_5d = open_days[5] if len(open_days) > 5 else None
d_20d = open_days[20] if len(open_days) > 20 else None

# 拉全市场 daily + daily_basic
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
    db_latest = {r["ts_code"]: r for r in
                 tushare("daily_basic", trade_date=latest,
                         fields="ts_code,close,turnover_rate,pe_ttm,pb,dv_ttm,total_mv")}

daily_latest = {r["ts_code"]: r for r in
                tushare("daily", trade_date=latest, fields="ts_code,close,amount,pct_chg")}
daily_prev = {r["ts_code"]: r for r in
              tushare("daily", trade_date=prev_day, fields="ts_code,close")} if prev_day else {}
daily_5d = {r["ts_code"]: r for r in
            tushare("daily", trade_date=d_5d, fields="ts_code,close,amount")} if d_5d else {}
daily_20d = {r["ts_code"]: r for r in
             tushare("daily", trade_date=d_20d, fields="ts_code,close,amount")} if d_20d else {}

# ====================================================================
# Header
# ====================================================================
import datetime as _dt
now = _dt.datetime.now()
print()
print("╔════════════════════════════════════════════════════════════════════════╗")
print(f"║  📅 DAILY BRIEF  ·  {now.strftime('%Y-%m-%d %H:%M')}  ·  数据日: {latest}".ljust(73) + "║")
print("╚════════════════════════════════════════════════════════════════════════╝")

# ====================================================================
# §1 持仓健康度 + 信号触发
# ====================================================================
if mode in ("full", "holdings", "alerts"):
    print(f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"  §1. 持仓健康度  ·  信号检测")
    print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    holdings_flat = all_holdings()
    total_w = total_weight()
    # 累计贡献
    portfolio_pnl = 0.0
    alerts = []  # 收集所有信号

    def fmt_pct(v, w=7):
        if v is None: return "n/a".rjust(w)
        return f"{v:+{w-1}.2f}%"

    def signals_for(ts_code, tier, cost_price, cur_close, r1w):
        """针对一只持仓产生信号列表."""
        sigs = []
        if cur_close is None or cost_price is None: return sigs
        pnl_pct = (cur_close - cost_price) / cost_price * 100
        # 止损
        stop_threshold = -15 if tier.startswith("基础") else -10
        if pnl_pct <= stop_threshold:
            sigs.append(("🚨", "STOP_LOSS",
                         f"跌破止损 {stop_threshold}%, 当前 {pnl_pct:+.1f}%, 建议清仓"))
        # 止盈
        take_threshold = 50 if tier.startswith("基础") else 30
        if pnl_pct >= take_threshold:
            sigs.append(("✅", "TAKE_PROFIT",
                         f"达到止盈 +{take_threshold}%, 当前 {pnl_pct:+.1f}%, 建议减仓 1/3"))
        # 减仓 (末期情绪顶)
        if r1w is not None and r1w > 15:
            sigs.append(("⚠️", "REDUCE",
                         f"1W 涨幅 {r1w:+.1f}% > +15%, 警惕末期加速, 建议减 30%"))
        # 加仓机会 (仅基础仓)
        if tier.startswith("基础") and pnl_pct <= -8 and pnl_pct > stop_threshold:
            sigs.append(("💡", "ADD",
                         f"回调到 {pnl_pct:+.1f}%, 若基本面未变, 可加 1/3 仓"))
        return sigs

    for tier, stocks in HOLDINGS.items():
        tier_pnl_weighted = 0.0
        tier_w = sum(w for _, _, w, _ in stocks)
        print(f"\n  ▣ {tier}  (权重 {tier_w:.1f}%)")
        print(f"    {'代码':<12}{'名称':<10}{'成本':>9}{'当前':>9}{'今日':>9}{'1W':>9}{'P&L':>9}  │  信号")
        for code, name, w, cost_price in stocks:
            dl = daily_latest.get(code, {})
            db = db_latest.get(code, {})
            cur = to_float(dl.get("close")) or to_float(db.get("close"))
            pct_chg_today = to_float(dl.get("pct_chg"))
            r1w = ret_pct(cur, daily_5d.get(code, {}).get("close"))
            pnl = ((cur - cost_price) / cost_price * 100) if cur else None
            if pnl is not None:
                tier_pnl_weighted += pnl * (w / 100)

            sigs = signals_for(code, tier, cost_price, cur, r1w)
            sig_str = "  ".join(icon for icon, _sig, _msg in sigs)
            for s in sigs: alerts.append((tier, code, name, s))

            print(f"    {code:<12}{name[:8]:<10} ¥{cost_price:>7.2f} ¥{cur:>7.2f} "
                  f"{fmt_pct(pct_chg_today):>8} {fmt_pct(r1w):>8} "
                  f"{fmt_pct(pnl):>8}  │  {sig_str}")

        # 该仓位贡献
        tier_contrib_pct = tier_pnl_weighted  # (pnl_pct × weight_pct / 100) aggregated
        portfolio_pnl += tier_contrib_pct
        print(f"    ── {tier} 合计 P&L 贡献: {tier_pnl_weighted:+.2f}% (占总资产)")

    print(f"\n  🎯 组合总 P&L: {portfolio_pnl:+.2f}% (总权重 {total_w:.0f}% + 现金 {100-total_w:.0f}%)")

    # Alert summary
    if alerts and mode != "themes":
        print(f"\n  🔔 触发信号 ({len(alerts)} 条):")
        for tier, code, name, sig in alerts:
            icon, sig_type, msg = sig
            print(f"    {icon}  [{tier}] {code} {name}  ·  {sig_type}")
            print(f"        {msg}")

# ====================================================================
# §2 组合总览 (alerts 模式跳过)
# ====================================================================
if mode == "alerts":
    if not alerts:
        print(f"\n  ✅ 无触发信号, 持仓整体稳定")
    sys.exit(0)

# ====================================================================
# §3 主题轮动 (当日 + 变化)
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
        r_today, r_1w = [], []
        amts = []
        for code, _name in stocks:
            dl = daily_latest.get(code, {})
            d5 = daily_5d.get(code, {})
            if to_float(dl.get("pct_chg")) is not None:
                r_today.append(to_float(dl.get("pct_chg")))
            r1w = ret_pct(dl.get("close"), d5.get("close"))
            if r1w is not None: r_1w.append(r1w)
            amts.append((to_float(dl.get("amount")) or 0) / 1e5)
        concept_stats.append({
            "name": concept,
            "avg_today": sum(r_today)/len(r_today) if r_today else None,
            "avg_1w":    sum(r_1w)/len(r_1w) if r_1w else None,
            "total_amt": sum(amts),
        })

    # 按今日涨跌排序
    concept_stats.sort(key=lambda s: -(s["avg_today"] or -1e9))

    print(f"\n  {'排名':<4}{'概念':<28}{'今日均涨':>10}{'5日均涨':>10}{'日成交(亿)':>14}")
    print(f"  {'-' * 65}")
    def mark(v, thr=0):
        if v is None: return "  "
        if v > abs(thr) + 2: return "🔥"
        if v < -abs(thr) - 2: return "🧊"
        return "  "
    for i, cs in enumerate(concept_stats[:5], 1):
        tag = mark(cs["avg_today"])
        today_str = f"{cs['avg_today']:+.2f}%" if cs["avg_today"] is not None else "n/a"
        w1_str = f"{cs['avg_1w']:+.1f}%" if cs["avg_1w"] is not None else "n/a"
        print(f"  {tag}{i:>2}. {cs['name']:<26} {today_str:>9} {w1_str:>9} {cs['total_amt']:>11.0f}")
    print(f"  {'-' * 65}")
    # Bottom 3
    for i, cs in enumerate(concept_stats[-3:], len(concept_stats) - 2):
        tag = mark(cs["avg_today"])
        today_str = f"{cs['avg_today']:+.2f}%" if cs["avg_today"] is not None else "n/a"
        w1_str = f"{cs['avg_1w']:+.1f}%" if cs["avg_1w"] is not None else "n/a"
        print(f"  {tag}{i:>2}. {cs['name']:<26} {today_str:>9} {w1_str:>9} {cs['total_amt']:>11.0f}")

# ====================================================================
# §4 美股隔夜 (只显示, 不分析)
# ====================================================================
if mode == "full":
    print(f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"  §4. 美股隔夜 (AI 链跨市场锚点)")
    print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"  {'ticker':<8}{'名称':<15}{'价格':>9}{'开盘':>9}{'区间':>18}")
    print(f"  {'-' * 60}")
    for ticker, name in US_ANCHORS:
        try:
            res = subprocess.run(["bash", f"{here}/quote.sh", ticker],
                                 capture_output=True, text=True, timeout=10)
            if res.returncode == 0:
                first_line = res.stdout.strip().split("\n")[0]
                # Parse "NVDA.US | price=200.96 open=199.89 high=201.05 low=198.61 vol=... ts=..."
                fields = dict(f.split("=", 1) for f in first_line.split(" | ")[1].split(" ")
                              if "=" in f)
                price = fields.get("price", "?")
                open_p = fields.get("open", "?")
                hi = fields.get("high", "?")
                lo = fields.get("low", "?")
                print(f"  {ticker:<8}{name:<15}${price:>8}  ${open_p:>7}  [${lo} — ${hi}]")
            else:
                print(f"  {ticker:<8}{name:<15}  [获取失败]")
        except Exception as e:
            print(f"  {ticker:<8}{name:<15}  [错误: {e}]")

# ====================================================================
# §5 政策信号 (过去 3 天 CCTV 联播 关键词)
# ====================================================================
if mode == "full":
    print(f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"  §5. 政策信号 (过去 3 日 CCTV 联播 关键词)")
    print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    try:
        # cctv_news 限速 (5/min), 3 天需要至少 60-120s (含 retry backoff)
        res = subprocess.run(
            ["bash", f"{here}/policy.sh", "days=3",
             "--grep=半导体|芯片|AI|算力|基础研究|科技|制造业|新能源|金融|消费"],
            capture_output=True, text=True, timeout=200,
        )
        if res.returncode == 0:
            # 提取有日期的部分
            lines = res.stdout.split("\n")
            for line in lines:
                if line.startswith("[20") or line.startswith("  ·"):
                    print(f"  {line}")
        else:
            print(f"  [policy.sh 获取失败]")
    except subprocess.TimeoutExpired:
        print(f"  [policy.sh 超时, 可能 cctv_news API 限速]")
    except Exception as e:
        print(f"  [错误: {e}]")

# ====================================================================
# §6 观察清单动态
# ====================================================================
if mode == "full":
    print(f"\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"  §6. 观察清单动态")
    print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"  {'代码':<12}{'名称':<10}{'当前':>9}{'今日':>9}{'1W':>9}{'PE':>7}")
    print(f"  {'-' * 60}")
    for code, name in WATCHLIST:
        dl = daily_latest.get(code, {})
        db = db_latest.get(code, {})
        cur = to_float(dl.get("close")) or to_float(db.get("close"))
        pct_chg_today = to_float(dl.get("pct_chg"))
        r1w = ret_pct(cur, daily_5d.get(code, {}).get("close"))
        pe = to_float(db.get("pe_ttm"))

        # 大涨大跌 flag
        flag = ""
        if pct_chg_today is not None:
            if pct_chg_today > 5: flag = "🔥"
            elif pct_chg_today < -5: flag = "🧊"
        if r1w is not None and r1w > 20: flag += "⚡"

        cur_str = f"¥{cur:.2f}" if cur else "n/a"
        pe_str = f"{pe:.1f}" if pe else "n/a"
        print(f"  {code:<12}{name[:8]:<10}{cur_str:>8} {fmt_pct(pct_chg_today):>8} "
              f"{fmt_pct(r1w):>8} {pe_str:>6}  {flag}")

print()
print("  💡 提示:")
if mode == "full":
    print("     - 持仓信号触发请看 §1 末尾")
    print("     - 主题轮动看 §3 是否有新的 🔥 (今日突破 +2%)")
    print("     - 美股隔夜异动 → 影响 A 股 AI 链开盘")
    print("     - 每周五建议额外跑 funnel.sh + momentum.sh 刷新候选池")
print()
PY
